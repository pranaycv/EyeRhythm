/**
 * Live Settings dock: Alert group (ORI + poor-rhythm minutes) and
 * Look & blink group (writing / turn angle, blink sensitivity).
 * Changes apply immediately; each committed change is written to audit.json.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchAudit,
  fetchCameras,
  fetchPreferences,
  fetchSettings,
  savePreferences,
  saveSettings,
  selectCamera,
  type AuditEntry,
  type CameraChoice,
  type CameraOption,
  type LiveSettings,
} from "../api";

const DEFAULTS: LiveSettings = {
  ori_threshold: 50,
  alert_low_minutes: 3,
  writing_angle_deg: 20,
  turn_angle_deg: 50,
  close_delta: 0.18,
};

export function SettingsPanel() {
  const [values, setValues] = useState<LiveSettings>(DEFAULTS);
  const [auditLog, setAuditLog] = useState<AuditEntry[]>([]);
  const timerRef = useRef<number | null>(null);
  const pendingRef = useRef<Partial<LiveSettings>>({});

  const [restoring, setRestoring] = useState(false);
  const [cameras, setCameras] = useState<CameraOption[]>([]);
  const [cameraIndex, setCameraIndex] = useState<CameraChoice | null>(null);
  const [cameraBusy, setCameraBusy] = useState(false);
  const [skipStartup, setSkipStartup] = useState(false);
  const skipSaveGen = useRef(0);

  const reloadAudit = useCallback(() => {
    void fetchAudit(8)
      .then(setAuditLog)
      .catch(() => {
        /* keep last */
      });
  }, []);

  useEffect(() => {
    let cancelled = false;
    void fetchSettings()
      .then((s) => {
        if (!cancelled) setValues({ ...DEFAULTS, ...s });
      })
      .catch(() => {
        /* defaults */
      });
    reloadAudit();
    void fetchCameras()
      .then((payload) => {
        if (!cancelled) {
          setCameras(payload.cameras);
          setCameraIndex(
            payload.mode === "hybrid" ? "hybrid" : payload.selected,
          );
        }
      })
      .catch(() => {
        /* no camera list */
      });
    void fetchPreferences()
      .then((prefs) => {
        // A click that already started must not be overwritten by this fetch.
        if (!cancelled && skipSaveGen.current === 0) {
          setSkipStartup(Boolean(prefs.skip_startup_screens));
        }
      })
      .catch(() => {
        /* default: startup screens stay on */
      });
    return () => {
      cancelled = true;
      if (timerRef.current != null) window.clearTimeout(timerRef.current);
    };
  }, [reloadAudit]);

  const commit = useCallback(
    (patch: Partial<LiveSettings>) => {
      pendingRef.current = { ...pendingRef.current, ...patch };
      if (timerRef.current != null) window.clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(() => {
        const send = pendingRef.current;
        pendingRef.current = {};
        void saveSettings(send)
          .then((s) => {
            setValues((prev) => ({ ...prev, ...s }));
            reloadAudit();
          })
          .catch(() => {
            /* engine restarting */
          });
      }, 220);
    },
    [reloadAudit],
  );

  const setNum = (key: keyof LiveSettings, raw: number) => {
    if (!Number.isFinite(raw)) return;
    setValues((prev) => ({ ...prev, [key]: raw }));
    commit({ [key]: raw });
  };

  const restoreDefaults = () => {
    if (restoring) return;
    if (timerRef.current != null) window.clearTimeout(timerRef.current);
    pendingRef.current = {};
    setRestoring(true);
    setValues(DEFAULTS);
    void saveSettings({ ...DEFAULTS })
      .then((s) => {
        setValues({ ...DEFAULTS, ...s });
        reloadAudit();
      })
      .catch(() => {
        /* engine restarting */
      })
      .finally(() => setRestoring(false));
  };

  const onSkipStartup = () => {
    const next = !skipStartup;
    const gen = ++skipSaveGen.current;
    setSkipStartup(next);
    void savePreferences({ skip_startup_screens: next })
      .then((prefs) => {
        if (skipSaveGen.current !== gen) return;
        setSkipStartup(Boolean(prefs.skip_startup_screens));
      })
      .catch(() => {
        if (skipSaveGen.current !== gen) return;
        setSkipStartup(!next);
      });
  };

  const onCameraChange = (choice: CameraChoice) => {
    if (cameraBusy) return;
    setCameraBusy(true);
    setCameraIndex(choice);
    void selectCamera(choice)
      .catch(() => {
        /* keep last selection */
      })
      .finally(() => setCameraBusy(false));
  };

  return (
    <div className="settings">
      <div className="settings__intro">
        <p className="settings__help">
          Default settings can be changed to suit your convenience and your
          experience with the app.
        </p>
        <button
          type="button"
          className="settings__restore"
          disabled={restoring}
          onClick={restoreDefaults}
        >
          {restoring ? "Restoring…" : "Restore defaults"}
        </button>
      </div>
      <section className="settings__group" aria-labelledby="settings-startup">
        <h3 id="settings-startup" className="settings__group-title">
          Startup
        </h3>
        <button
          type="button"
          role="switch"
          aria-checked={skipStartup}
          className={`settings__switch${skipStartup ? " is-on" : ""}`}
          onClick={onSkipStartup}
        >
          <span className="settings__switch-track" aria-hidden>
            <span className="settings__switch-knob" />
          </span>
          <span className="settings__check-copy">
            <span className="settings__name">Skip startup screens</span>
            <p className="settings__help">
              Opens the dashboard directly the next time EyesRhythm starts.
              The comfort check and the Before you start notes stay off. If
              several cameras are connected and none is chosen yet, that
              camera question still appears once.
            </p>
          </span>
        </button>
      </section>
      {cameras.length > 0 ? (
        <label className="settings__field">
          <div className="settings__row">
            <span className="settings__name">Camera</span>
          </div>
          <select
            className="settings__select"
            value={
              cameraIndex === "hybrid" && cameras.length > 1
                ? "hybrid"
                : String(cameraIndex ?? cameras[0]?.index ?? 0)
            }
            disabled={cameraBusy}
            onChange={(e) => {
              const raw = e.target.value;
              if (raw === "hybrid") onCameraChange("hybrid");
              else onCameraChange(Number(raw));
            }}
          >
            {cameras.length > 1 ? (
              <option value="hybrid">Hybrid (utilizes CPU extensively)</option>
            ) : null}
            {cameras.map((cam) => (
              <option key={cam.index} value={cam.index}>
                {cam.label}
              </option>
            ))}
          </select>
          <p className="settings__help">
            {cameras.length > 1 ? (
              <>
                One webcam, or Hybrid to use every built-in and USB camera at
                once (not a phone). Hybrid keeps full frame rate by running
                every camera together (utilizes CPU extensively). Each tick
                grabs all cameras at the same instant, then counts blinks on
                the front-facing view. The engine restarts when you change
                this.
              </>
            ) : (
              <>
                The webcam EyesRhythm is using. Plug in another desk camera
                to choose Hybrid.
              </>
            )}
          </p>
        </label>
      ) : null}
      <section className="settings__group" aria-labelledby="settings-alert">
        <h3 id="settings-alert" className="settings__group-title">
          Alert
        </h3>
        <label className="settings__field">
          <div className="settings__row">
            <span className="settings__name">ORI threshold</span>
            <span className="settings__value">{Math.round(values.ori_threshold)}</span>
          </div>
          <input
            type="range"
            min={20}
            max={80}
            step={1}
            value={values.ori_threshold}
            onChange={(e) => setNum("ori_threshold", Number(e.target.value))}
          />
          <p className="settings__help">
            Score that counts as poor rhythm (default 50). Alert uses this
            line; the red dotted Trace line moves with it.
          </p>
        </label>
        <label className="settings__field">
          <div className="settings__row">
            <span className="settings__name">Poor-rhythm minutes</span>
            <span className="settings__value">
              {Math.round(values.alert_low_minutes)} min
            </span>
          </div>
          <input
            type="range"
            min={1}
            max={5}
            step={1}
            value={values.alert_low_minutes}
            onChange={(e) => setNum("alert_low_minutes", Number(e.target.value))}
          />
          <p className="settings__help">
            How many minutes of low ORI in the last 5 must accumulate before
            Alert fires (default 3). Visual flashes a translucent icon on every
            screen when the app turns orange, about three times a minute; Sound
            plays a short tone. 1 fires sooner;
            5 waits for almost the whole Trace window.
          </p>
        </label>
      </section>

      <section className="settings__group" aria-labelledby="settings-look">
        <h3 id="settings-look" className="settings__group-title">
          Look &amp; blink
        </h3>
        <label className="settings__field">
          <div className="settings__row">
            <span className="settings__name">Writing angle</span>
            <span className="settings__value">
              {Math.round(values.writing_angle_deg)}°
            </span>
          </div>
          <input
            type="range"
            min={10}
            max={40}
            step={1}
            value={values.writing_angle_deg}
            onChange={(e) => setNum("writing_angle_deg", Number(e.target.value))}
          />
          <p className="settings__help">
            Downward look treated as off-screen. At the default 20°, a glance
            of 20° or more pauses blink counting, regularity, and Alert, and
            cuts Trace. Lower (10–15°) is stricter for close keyboard work;
            higher (30–40°) keeps counting while you look at notes.
          </p>
        </label>
        <label className="settings__field">
          <div className="settings__row">
            <span className="settings__name">Turn angle</span>
            <span className="settings__value">
              {Math.round(values.turn_angle_deg)}°
            </span>
          </div>
          <input
            type="range"
            min={20}
            max={80}
            step={1}
            value={values.turn_angle_deg}
            onChange={(e) => setNum("turn_angle_deg", Number(e.target.value))}
          />
          <p className="settings__help">
            Left or right look treated as off-screen. At the default 50°,
            turning past 50° pauses counting and Alert, and cuts Trace. Lower
            is stricter; higher allows a wider side glance.
          </p>
        </label>
        <label className="settings__field">
          <div className="settings__row">
            <span className="settings__name">Blink sensitivity</span>
            <span className="settings__value">
              {values.close_delta.toFixed(2)}
            </span>
          </div>
          <input
            type="range"
            min={0.1}
            max={0.3}
            step={0.01}
            value={values.close_delta}
            onChange={(e) => setNum("close_delta", Number(e.target.value))}
          />
          <p className="settings__help">
            How far the eyes must close above the open-eye baseline before a
            blink counts. Lower catches weaker or incomplete blinks. Higher
            needs a clearer closure (fewer false counts; may miss soft blinks
            or glasses).
          </p>
        </label>
      </section>

      <section className="settings__audit" aria-label="Settings audit log">
        <div className="settings__name">Recent changes</div>
        {auditLog.length === 0 ? (
          <p className="settings__help">No changes yet this device.</p>
        ) : (
          <ul className="settings__audit-list">
            {auditLog.map((e, i) => (
              <li key={`${e.at}-${e.setting}-${i}`}>
                <span className="settings__audit-at">{e.at.replace("T", " ")}</span>
                <span className="settings__audit-what">
                  {e.label || e.setting}: {String(e.from)} → {String(e.to)}
                </span>
              </li>
            ))}
          </ul>
        )}
        <p className="settings__help">
          Written to a local audit.json with date and time. Nothing is uploaded.
        </p>
      </section>
    </div>
  );
}

import { useEffect, useMemo, useState } from "react";
import { fetchQuotes, type QuoteItem } from "../api";

interface Props {
  practice: boolean;
  practicePhase: string;
  countdownS: number;
  remainingS: number;
}

const FALLBACK: QuoteItem[] = [
  {
    text: "Be yourself; everyone else is already taken.",
    author: "Oscar Wilde",
  },
  {
    text: "In the middle of difficulty lies opportunity.",
    author: "Albert Einstein",
  },
  {
    text: "It always seems impossible until it is done.",
    author: "Nelson Mandela",
  },
  {
    text: "Peace begins with a smile.",
    author: "Mother Teresa",
  },
  {
    text: "Wherever you go, go with all your heart.",
    author: "Confucius",
  },
  {
    text: "What you seek is seeking you.",
    author: "Rumi",
  },
  {
    text: "Even the darkest night will end and the sun will rise.",
    author: "Victor Hugo",
  },
  {
    text: "Adopt the pace of nature: her secret is patience.",
    author: "Ralph Waldo Emerson",
  },
];

const QUOTE_ROTATE_MS = 30_000;

const PRACTICE_INSTRUCTION =
  "Follow the moving ball with your eyes. Blink gently on each beep, keep your head still, and enjoy the quotes below.";

/**
 * Full-width gray card: “Information & Quote”.
 */
export function InfoQuoteCard({
  practice,
  practicePhase,
  countdownS,
  remainingS,
}: Props) {
  const [quotes, setQuotes] = useState<QuoteItem[]>(FALLBACK);
  const [quoteIndex, setQuoteIndex] = useState(0);

  const inCountdown = practice && practicePhase === "countdown";
  const inActive = practice && practicePhase === "active";
  const inSummary = practice && practicePhase === "summary";
  const inPractice = inCountdown || inActive || inSummary;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchQuotes();
        if (!cancelled && data.quotes?.length) {
          setQuotes(data.quotes);
        }
      } catch {
        /* fallbacks */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    setQuoteIndex(Math.floor(Math.random() * Math.max(1, quotes.length)));
  }, [practice, quotes.length]);

  useEffect(() => {
    if (quotes.length === 0) return;
    const id = window.setInterval(() => {
      setQuoteIndex((i) => (i + 1) % quotes.length);
    }, QUOTE_ROTATE_MS);
    return () => window.clearInterval(id);
  }, [quotes.length]);

  const item = useMemo(
    () => quotes[quoteIndex % quotes.length] ?? FALLBACK[0],
    [quotes, quoteIndex],
  );

  const timerLabel = inCountdown
    ? `Starts in ${Math.max(0, Math.ceil(countdownS))}s`
    : inActive
      ? formatClock(remainingS)
      : null;

  const progress = inActive
    ? Math.min(1, Math.max(0, remainingS / 120))
    : inCountdown
      ? Math.min(1, Math.max(0, countdownS / 20))
      : 0;

  return (
    <article
      className={`info-quote-card ${inPractice ? "info-quote-card--practice" : ""} ${
        inCountdown ? "info-quote-card--countdown" : ""
      }`}
      role="region"
      aria-label="Information and quote"
    >
      <header className="info-quote-card__head">
        <span className="info-quote-card__title">Information &amp; Quote</span>
        {timerLabel ? (
          <span className="info-quote-card__timer" title="Practice timer">
            {timerLabel}
          </span>
        ) : null}
      </header>

      {inPractice ? (
        <p
          className={`info-quote-card__instruction ${
            inCountdown ? "info-quote-card__instruction--flash" : ""
          }`}
        >
          {PRACTICE_INSTRUCTION}
        </p>
      ) : (
        <ul className="info-quote-card__tips">
          <li>
            <strong>Beep</strong> — sound on each blink
          </li>
          <li>
            <strong>Alert</strong> — warn if rate stays low (~10/min for 2 of last 5 min)
          </li>
          <li>
            <strong>Sweep</strong> — save your eight extreme look angles
          </li>
          <li>
            <strong>Practice</strong> — 2‑min blink coach
          </li>
        </ul>
      )}

      <div className="info-quote-card__quote-box">
        <p key={`${quoteIndex}-t`} className="info-quote-card__quote">
          “{item.text}”
        </p>
        <p key={`${quoteIndex}-a`} className="info-quote-card__author">
          — {item.author}
        </p>
      </div>

      {inPractice ? (
        <div className="info-quote-card__bar" aria-hidden>
          <div
            className="info-quote-card__bar-fill"
            style={{ transform: `scaleX(${progress})` }}
          />
        </div>
      ) : null}
    </article>
  );
}

function formatClock(remainingS: number): string {
  const secs = Math.max(0, Math.ceil(remainingS));
  const mm = Math.floor(secs / 60);
  const ss = secs % 60;
  return `${mm}:${ss.toString().padStart(2, "0")}`;
}

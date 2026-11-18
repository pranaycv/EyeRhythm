/**
 * Site copy and knobs, edit here as you iterate.
 *
 * The app is free. Mac and Windows installers are linked below.
 * They are too large for this host, so the buttons download the
 * public files from the GitHub release. On a new release, attach
 * the new files to the tag and change the names in ``downloads``. The Dodo
 * checkout URLs stay in ``dodo`` so a paid gate can be turned
 * back on without hunting for them. The app itself only asks
 * for a key when the license gate is on.
 */

/** Hold the deployed site to a coming-soon screen. Local `astro dev` ignores this. */
const comingSoonPublic = false;

const DOWNLOAD_BASE =
  "https://github.com/pranaycv/EyeRhythm/releases/download/v1.6.0";

export const site = {
  name: "EyesRhythm",
  tagline: "Blink rate detector for long screen days",
  description:
    "EyesRhythm is a free, private webcam blink rate detector for Mac and Windows. It scores how often and how steadily you blink during screen work, and Poise shows which way you turned your head so the neck and shoulders are not stuck in one pose.",
  url:
    typeof import.meta.env.SITE === "string" && import.meta.env.SITE
      ? String(import.meta.env.SITE).replace(/\/$/, "")
      : "http://localhost:4321",
  contactEmail: "hello@eyesrhythm.com",
  cta: "Download",
  /** False on localhost (`astro dev`) even if the public site is still held. */
  comingSoon: comingSoonPublic && !import.meta.env.DEV,
  /** True: the site offers the Mac and Windows installers directly. */
  downloadsOpen: true,
  price: {
    amount: 0,
    currency: "USD",
    label: "Free",
    cadence: "free",
    updates: "Free for Mac and Windows",
  },
  version: "1.6.0",
  recommendedMachine:
    "Tested on Intel Core i7 and on Mac processors. We recommend this configuration.",
  downloads: {
    mac: {
      href: `${DOWNLOAD_BASE}/EyesRhythm-1.6.0.dmg`,
      file: "EyesRhythm-1.6.0.dmg",
      size: "93 MB",
      label: "macOS",
    },
    windows: {
      href: `${DOWNLOAD_BASE}/EyesRhythm-Setup-1.6.0.exe`,
      file: "EyesRhythm-Setup-1.6.0.exe",
      size: "85 MB",
      label: "Windows",
    },
  },
  /**
   * Unused while the app is free. Kept so checkout can be restored
   * without recreating the Dodo product links.
   */
  dodo: {
    checkoutMac:
      "https://checkout.dodopayments.com/buy/pdt_0Nmc1Cv8LQYff3wqM6ULl?quantity=1",
    checkoutWindows:
      "https://checkout.dodopayments.com/buy/pdt_0Nmc1TZ9g5VKLY81Xi6pG?quantity=1",
  },
} as const;

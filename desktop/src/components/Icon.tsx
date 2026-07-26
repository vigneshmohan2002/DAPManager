import type { ReactNode } from "react";

export type IconName =
  | "albums"
  | "artists"
  | "audit"
  | "back"
  | "close"
  | "contributions"
  | "downloads"
  | "duplicates"
  | "fleet"
  | "heart"
  | "home"
  | "listening"
  | "lyrics"
  | "mini"
  | "next"
  | "orphans"
  | "pause"
  | "play"
  | "playlist"
  | "previous"
  | "queue"
  | "releases"
  | "repeat"
  | "search"
  | "settings"
  | "shuffle"
  | "sleep"
  | "songs"
  | "suggest"
  | "sync";

type Props = {
  name: IconName;
  size?: number;
  className?: string;
};

function glyph(name: IconName): ReactNode {
  switch (name) {
    case "home":
      return (
        <>
          <path d="m3.5 10 8.5-7 8.5 7" />
          <path d="M5.5 9v11h13V9M9.5 20v-6h5v6" />
        </>
      );
    case "albums":
      return (
        <>
          <rect x="3.5" y="3.5" width="17" height="17" rx="2" />
          <circle cx="12" cy="12" r="4.25" />
          <circle cx="12" cy="12" r=".8" fill="currentColor" stroke="none" />
        </>
      );
    case "artists":
      return (
        <>
          <circle cx="12" cy="8" r="3.25" />
          <path d="M5.5 20c.35-4.1 2.55-6.2 6.5-6.2s6.15 2.1 6.5 6.2" />
        </>
      );
    case "songs":
      return (
        <>
          <path d="M9 18V5.5l10-2V16" />
          <ellipse cx="6.5" cy="18.3" rx="2.7" ry="2.2" />
          <ellipse cx="16.5" cy="16.3" rx="2.7" ry="2.2" />
        </>
      );
    case "listening":
      return (
        <>
          <path d="M4 19V9M9.3 19V5M14.7 19v-7M20 19V3" />
        </>
      );
    case "downloads":
      return (
        <>
          <path d="M12 3v12M7.5 11l4.5 4.5 4.5-4.5" />
          <path d="M4 20h16" />
        </>
      );
    case "releases":
      return (
        <>
          <circle cx="12" cy="12" r="8.5" />
          <path d="M12 7.5v9M7.5 12h9" />
        </>
      );
    case "audit":
      return (
        <>
          <path d="M8 4h8M9 2.8h6v3H9zM6 4.5H4.5v16h15v-16H18" />
          <path d="m8 13 2.2 2.2L16.5 9" />
        </>
      );
    case "duplicates":
      return (
        <>
          <rect x="7" y="7" width="12.5" height="12.5" rx="1.8" />
          <path d="M16.5 7V4.5h-12v12H7" />
        </>
      );
    case "orphans":
      return (
        <>
          <path d="M12 3.5 3.8 7.8v8.4L12 20.5l8.2-4.3V7.8z" />
          <path d="M3.8 7.8 12 12l8.2-4.2M12 12v8.5" />
        </>
      );
    case "fleet":
      return (
        <>
          <rect x="3.5" y="5" width="17" height="11" rx="2" />
          <path d="M8 20h8M12 16v4M8 9h8" />
        </>
      );
    case "contributions":
      return (
        <>
          <path d="M8 12h8M12 8v8" />
          <circle cx="12" cy="12" r="8.5" />
        </>
      );
    case "sync":
      return (
        <>
          <path d="M19.5 8.5A8 8 0 0 0 6 5.5L3.5 8" />
          <path d="M3.5 4.5V8h3.5M4.5 15.5A8 8 0 0 0 18 18.5l2.5-2.5" />
          <path d="M20.5 19.5V16H17" />
        </>
      );
    case "suggest":
      return (
        <>
          <path d="M9 18h6M9.5 21h5" />
          <path d="M8.5 16c-.3-1.6-3-3.1-3-6.6a6.5 6.5 0 1 1 13 0c0 3.5-2.7 5-3 6.6z" />
        </>
      );
    case "settings":
      return (
        <>
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1-2.9 2.9-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21H10v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1-2.9-2.9.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3v-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1 2.9-2.9.1.1A1.6 1.6 0 0 0 9 4.6a1.6 1.6 0 0 0 1-1.5V3h4v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1 2.9 2.9-.1.1a1.6 1.6 0 0 0-.3 1.8 1.6 1.6 0 0 0 1.5 1h.1v4h-.1a1.6 1.6 0 0 0-1.5 1Z" />
        </>
      );
    case "search":
      return (
        <>
          <circle cx="10.5" cy="10.5" r="6.5" />
          <path d="m15.5 15.5 5 5" />
        </>
      );
    case "playlist":
      return (
        <>
          <path d="M4 6h10M4 11h10M4 16h7" />
          <path d="M17 8v9.5" />
          <ellipse cx="14.5" cy="18" rx="2.5" ry="2" />
        </>
      );
    case "heart":
      return (
        <path d="M20.5 9.1c0 5.1-8.5 10.1-8.5 10.1S3.5 14.2 3.5 9.1C3.5 5.8 7.6 3.7 12 7c4.4-3.3 8.5-1.2 8.5 2.1Z" />
      );
    case "back":
      return <path d="m14.5 5-7 7 7 7" />;
    case "close":
      return <path d="m6 6 12 12M18 6 6 18" />;
    case "shuffle":
      return (
        <>
          <path d="M4 7h2.3c4.5 0 6.8 10 11.4 10H20" />
          <path d="m17 14 3 3-3 3M4 17h2.3c1.5 0 2.7-1.2 3.8-2.8M13.8 9.8C15 8.2 16.2 7 17.7 7H20" />
          <path d="m17 4 3 3-3 3" />
        </>
      );
    case "previous":
      return (
        <>
          <path d="M6.5 5v14" />
          <path d="m18 6-9 6 9 6z" fill="currentColor" stroke="none" />
        </>
      );
    case "play":
      return <path d="m8 5 11 7-11 7z" fill="currentColor" stroke="none" />;
    case "pause":
      return (
        <>
          <rect x="7" y="5" width="3.5" height="14" rx=".8" fill="currentColor" stroke="none" />
          <rect x="13.5" y="5" width="3.5" height="14" rx=".8" fill="currentColor" stroke="none" />
        </>
      );
    case "next":
      return (
        <>
          <path d="M17.5 5v14" />
          <path d="m6 6 9 6-9 6z" fill="currentColor" stroke="none" />
        </>
      );
    case "repeat":
      return (
        <>
          <path d="M17 4.5 20 7.5 17 10.5" />
          <path d="M4 11V9.5a2 2 0 0 1 2-2h14M7 19.5 4 16.5 7 13.5" />
          <path d="M20 13v1.5a2 2 0 0 1-2 2H4" />
        </>
      );
    case "sleep":
      return <path d="M19.5 15.5A8 8 0 0 1 8.5 4.5a8 8 0 1 0 11 11Z" />;
    case "lyrics":
      return (
        <>
          <path d="M9 18V5.5l10-2V16" />
          <ellipse cx="6.5" cy="18.3" rx="2.7" ry="2.2" />
          <ellipse cx="16.5" cy="16.3" rx="2.7" ry="2.2" />
        </>
      );
    case "queue":
      return (
        <>
          <path d="M5 6h14M5 12h14M5 18h10" />
          <circle cx="3" cy="6" r=".6" fill="currentColor" stroke="none" />
          <circle cx="3" cy="12" r=".6" fill="currentColor" stroke="none" />
          <circle cx="3" cy="18" r=".6" fill="currentColor" stroke="none" />
        </>
      );
    case "mini":
      return (
        <>
          <rect x="3.5" y="3.5" width="17" height="17" rx="2" />
          <path d="M9 15H6v3M15 9h3V6M6 18l4-4M18 6l-4 4" />
        </>
      );
  }
}

export default function Icon({ name, size = 16, className = "" }: Props) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {glyph(name)}
    </svg>
  );
}

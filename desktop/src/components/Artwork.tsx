import { useEffect, useState } from "react";

type Props = {
  src?: string | null;
  alt: string;
  className?: string;
  imageClassName?: string;
  fallbackLabel?: string;
  loading?: "eager" | "lazy";
};

export default function Artwork({
  src,
  alt,
  className = "",
  imageClassName = "",
  fallbackLabel = "No cover",
  loading = "lazy",
}: Props) {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [src]);

  const showImage = Boolean(src) && !failed;

  return (
    <span
      className={`relative block overflow-hidden bg-[var(--color-surface)] ${className}`}
    >
      {showImage ? (
        <img
          src={src ?? undefined}
          alt={alt}
          loading={loading}
          draggable={false}
          onError={() => setFailed(true)}
          className={`h-full w-full object-cover ${imageClassName}`}
        />
      ) : (
        <span className="absolute inset-0 grid place-items-center px-2 text-center text-[10px] text-[var(--color-text-muted)]">
          {fallbackLabel}
        </span>
      )}
    </span>
  );
}

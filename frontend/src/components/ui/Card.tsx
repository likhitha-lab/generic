import type { HTMLAttributes } from "react";

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  hoverable?: boolean;
}

/**
 * Standardized card surface - dark enterprise theme: surface color, soft
 * white-opacity border, minimal shadow, rounded corners, optional hover
 * elevation. Purely presentational (a plain <div>) - no functionality
 * attached.
 */
export function Card({ hoverable = false, className = "", ...props }: CardProps) {
  return (
    <div
      className={`bg-surface rounded-2xl border border-white/[0.08] shadow-sm shadow-black/20
        ${hoverable ? "transition-all duration-200 ease-out hover:shadow-md hover:shadow-black/30 hover:border-white/[0.14] hover:-translate-y-0.5" : ""}
        ${className}`}
      {...props}
    />
  );
}

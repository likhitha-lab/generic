import { forwardRef } from "react";
import type { ButtonHTMLAttributes } from "react";

type ButtonVariant = "primary" | "secondary" | "danger" | "ghost";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
}

/**
 * Standardized button - one source of truth for height/padding/radius/
 * typography/hover/active/disabled states across the app (previously
 * hand-duplicated, slightly differently, in every page). Purely
 * presentational: same <button> element, same props/behavior/onClick
 * semantics as a plain <button>, nothing added or removed functionally.
 */
const variantClasses: Record<ButtonVariant, string> = {
  primary:
    "bg-brand-600 text-white shadow-sm shadow-brand-900/40 hover:bg-brand-500 active:bg-brand-700 disabled:bg-white/[0.06] disabled:text-ink-muted disabled:shadow-none",
  secondary:
    "bg-transparent text-ink border border-white/[0.12] hover:bg-white/[0.04] hover:border-white/[0.2] active:bg-white/[0.08] disabled:text-ink-muted/50 disabled:bg-transparent disabled:border-white/[0.06]",
  danger:
    "bg-transparent text-danger-500 border border-danger-500/25 hover:bg-danger-500/10 hover:border-danger-500/40 active:bg-danger-500/15 disabled:text-danger-500/40 disabled:border-danger-500/10",
  ghost: "text-ink-muted hover:bg-white/[0.06] hover:text-ink active:bg-white/[0.1] disabled:text-ink-muted/40",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = "primary", className = "", disabled, ...props }, ref) => (
    <button
      ref={ref}
      disabled={disabled}
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold
        transition-all duration-150 ease-out active:scale-[0.98]
        disabled:cursor-not-allowed disabled:active:scale-100
        ${variantClasses[variant]} ${className}`}
      {...props}
    />
  ),
);
Button.displayName = "Button";

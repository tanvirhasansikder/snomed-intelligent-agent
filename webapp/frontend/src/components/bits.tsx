import { useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";
import { Link } from "react-router-dom";
import type { Ref } from "../api/client";
import { OUTCOME_TEXT, OUTCOME_TONE, TONE_SOFT, type Tone } from "../utils/labels";

export function Sctid({ id, className = "" }: { id: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard?.writeText(id).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1400); });
      }}
      className={`group inline-flex items-center gap-1.5 rounded px-1 -mx-1 sctid hover:bg-wash ${className}`}
      aria-label={`Copy concept ID ${id}`}
    >
      {id}
      {copied ? <Check size={12} className="text-supported" /> : <Copy size={12} className="opacity-0 group-hover:opacity-60" />}
    </button>
  );
}

export function Pill({ tone = "neutral", children, className = "" }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${TONE_SOFT[tone]} ${className}`}>
      {children}
    </span>
  );
}

export function Dot({ color }: { color: string }) {
  return <span aria-hidden className="inline-block h-2 w-2 shrink-0 rounded-full" style={{ background: color }} />;
}

export function OutcomeBadge({ outcome }: { outcome: string }) {
  return <Pill tone={OUTCOME_TONE[outcome] ?? "neutral"}>{OUTCOME_TEXT[outcome] ?? outcome}</Pill>;
}

export function ConceptLink({ c, className = "" }: { c: Ref; className?: string }) {
  return (
    <Link to={`/concept/${c.id}`} className={`text-ink underline decoration-rule-strong underline-offset-[3px] hover:decoration-ink ${className}`}>
      {c.name}
    </Link>
  );
}

export function Segmented<T extends string | number>({
  options, value, onChange, label, disabled,
}: {
  options: { value: T; label: string; hint?: string; disabled?: boolean }[];
  value: T; onChange: (v: T) => void; label: string; disabled?: boolean;
}) {
  return (
    <div role="radiogroup" aria-label={label} className={`inline-flex flex-wrap rounded-lg border border-rule bg-surface p-0.5 ${disabled ? "opacity-50" : ""}`}>
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={String(o.value)} type="button" role="radio" aria-checked={on} title={o.hint}
            disabled={disabled || o.disabled}
            onClick={() => onChange(o.value)}
            className={`h-8 rounded-md px-3 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${on ? "bg-ink text-white" : "text-ink-2 hover:bg-wash"}`}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export function Section({ title, aside, children, className = "" }: { title: string; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={className}>
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold text-ink-2">{title}</h2>
        {aside && <div className="text-xs text-ink-3">{aside}</div>}
      </div>
      {children}
    </section>
  );
}

export function ErrorNote({ title, detail }: { title: string; detail?: string }) {
  return (
    <div role="alert" className="rounded-lg border border-false/30 bg-false-soft px-4 py-3 text-sm text-false">
      <div className="font-medium">{title}</div>
      {detail && <div className="mt-0.5 text-ink-2">{detail}</div>}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-wash ${className}`} />;
}

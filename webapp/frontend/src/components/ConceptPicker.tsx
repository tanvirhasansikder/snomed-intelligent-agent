// A search-as-you-type combobox for choosing one concept.
import { useEffect, useRef, useState } from "react";
import { Command } from "cmdk";
import { useQuery } from "@tanstack/react-query";
import { Search, X } from "lucide-react";
import { api, type Ref } from "../api/client";
import { useDebounced } from "../utils/hooks";

export default function ConceptPicker({
  label, value, onChange, placeholder = "Search by name or concept ID", id,
}: { label: string; value: Ref | null; onChange: (c: Ref | null) => void; placeholder?: string; id: string }) {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const q = useDebounced(text.trim(), 180);
  const box = useRef<HTMLDivElement>(null);
  const { data = [], isFetching } = useQuery({ queryKey: ["search", q], queryFn: () => api.search(q, 8), enabled: q.length > 1 });

  useEffect(() => {
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  if (value) {
    return (
      <div>
        <span className="field-label" id={`${id}-label`}>{label}</span>
        <div className="flex h-11 items-center justify-between gap-2 rounded-lg border border-ink/70 bg-surface pl-3 pr-1.5" aria-labelledby={`${id}-label`}>
          <div className="min-w-0">
            <span className="block truncate text-[14.5px] font-medium">{value.name}</span>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <span className="sctid hidden sm:inline">{value.id}</span>
            <button type="button" onClick={() => { onChange(null); setText(""); }} className="rounded p-1.5 text-ink-3 hover:bg-wash hover:text-ink" aria-label={`Clear ${label}`}>
              <X size={15} />
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div ref={box} className="relative">
      <label htmlFor={id} className="field-label">{label}</label>
      <Command shouldFilter={false} loop className="relative">
        <div className="flex h-11 items-center gap-2 rounded-lg border border-rule bg-surface px-3 focus-within:border-ink focus-within:ring-2 focus-within:ring-focus/15">
          <Search size={16} className="shrink-0 text-ink-4" />
          <Command.Input
            id={id} value={text} onValueChange={(v) => { setText(v); setOpen(true); }} onFocus={() => setOpen(true)}
            onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
            placeholder={placeholder} className="h-full w-full bg-transparent text-[14.5px] outline-none placeholder:text-ink-4"
          />
          {isFetching && <span className="h-1.5 w-1.5 animate-pulseline rounded-full bg-ink-3" aria-hidden />}
        </div>
        {open && q.length > 1 && (
          <Command.List className="absolute left-0 right-0 top-[calc(100%+6px)] z-30 max-h-80 overflow-auto rounded-lg border border-rule bg-surface p-1 shadow-pop">
            {!isFetching && data.length === 0 && <Command.Empty className="px-3 py-3 text-sm text-ink-3">No concept matches “{q}”.</Command.Empty>}
            {data.map((h) => (
              <Command.Item
                key={h.id} value={h.id} onSelect={() => { onChange(h); setOpen(false); }}
                className="flex cursor-pointer items-center justify-between gap-3 rounded-md px-3 py-2"
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">{h.name}</span>
                  <span className="block truncate text-xs text-ink-3">{h.tag}{h.hierarchy ? `, in ${h.hierarchy.name}` : ""}</span>
                </span>
                <span className="sctid shrink-0">{h.id}</span>
              </Command.Item>
            ))}
          </Command.List>
        )}
      </Command>
    </div>
  );
}

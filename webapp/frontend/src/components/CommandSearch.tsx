// The global search (Ctrl+K or /): jump to any concept.
import { useEffect, useState } from "react";
import { Command } from "cmdk";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { CornerDownLeft, Search } from "lucide-react";
import { api } from "../api/client";
import { useDebounced } from "../utils/hooks";

export default function CommandSearch({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [text, setText] = useState("");
  const q = useDebounced(text.trim(), 150);
  const navigate = useNavigate();
  const { data = [], isFetching } = useQuery({ queryKey: ["search", q, 12], queryFn: () => api.search(q, 12), enabled: open && q.length > 1 });

  useEffect(() => { if (!open) setText(""); }, [open]);

  return (
    <Command.Dialog
      open={open} onOpenChange={onOpenChange} shouldFilter={false} loop label="Search concepts"
      overlayClassName="fixed inset-0 z-40 bg-ink/25 backdrop-blur-[2px]"
      contentClassName="fixed inset-x-0 top-[12vh] z-50 mx-auto w-[min(640px,calc(100vw-24px))] overflow-hidden rounded-xl border border-rule bg-surface shadow-pop animate-rise"
    >
      <div className="flex h-14 items-center gap-3 border-b border-rule px-4">
        <Search size={18} className="text-ink-3" />
        <Command.Input
          value={text} onValueChange={setText} autoFocus placeholder="Search 351,751 concepts by name, synonym or ID"
          className="h-full w-full bg-transparent text-md outline-none placeholder:text-ink-4"
        />
        <kbd className="hidden rounded border border-rule px-1.5 py-0.5 text-xs text-ink-3 sm:inline">Esc</kbd>
      </div>
      <Command.List className="max-h-[min(60vh,460px)] overflow-auto p-1.5">
        {q.length < 2 && (
          <div className="px-3 py-6 text-sm text-ink-3">
            Type at least two letters. Try <em>appendicitis</em>, <em>insulin</em> or <em>22298006</em>.
          </div>
        )}
        {q.length > 1 && !isFetching && data.length === 0 && (
          <Command.Empty className="px-3 py-6 text-sm text-ink-3">Nothing in the International view matches “{q}”.</Command.Empty>
        )}
        {data.length > 0 && (
          <Command.Group heading="Concepts">
            {data.map((h) => (
              <Command.Item
                key={h.id} value={h.id}
                onSelect={() => { onOpenChange(false); navigate(`/concept/${h.id}`); }}
                className="group flex cursor-pointer items-center justify-between gap-3 rounded-lg px-3 py-2.5"
              >
                <span className="min-w-0">
                  <span className="block truncate font-medium">{h.name}</span>
                  <span className="block truncate text-xs text-ink-3">{h.tag}{h.hierarchy && h.hierarchy.id !== h.id ? `, in ${h.hierarchy.name}` : ""}</span>
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  <span className="sctid">{h.id}</span>
                  <CornerDownLeft size={14} className="text-ink-4 opacity-0 group-data-[selected=true]:opacity-100" />
                </span>
              </Command.Item>
            ))}
          </Command.Group>
        )}
      </Command.List>
    </Command.Dialog>
  );
}

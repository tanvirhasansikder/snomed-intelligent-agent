import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { ChevronRight, MessageSquareText, Maximize2, Minimize2 } from "lucide-react";
import { api, ApiError, type Concept, type Ref } from "../api/client";
import HierarchyGraph, { type GraphEdge, type GraphNode } from "../components/HierarchyGraph";
import { ConceptLink, ErrorNote, Section, Sctid, Skeleton } from "../components/bits";
import { fmtInt } from "../utils/labels";
import { useTitle } from "../utils/hooks";

function Breadcrumb({ path }: { path: Ref[] }) {
  return (
    <nav aria-label="Path from the root" className="flex flex-wrap items-center gap-x-1 gap-y-1 text-sm text-ink-3">
      {path.map((p, i) => (
        <span key={p.id} className="flex items-center gap-1">
          {i > 0 && <ChevronRight size={13} className="text-ink-4" aria-hidden />}
          {i === path.length - 1 ? <span className="text-ink-2" aria-current="page">{p.name}</span>
            : <Link to={`/concept/${p.id}`} className="hover:text-ink hover:underline">{p.name}</Link>}
        </span>
      ))}
    </nav>
  );
}

function Fact({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div title={hint}>
      <dt className="text-xs text-ink-3">{label}</dt>
      <dd className="mt-0.5 text-md font-semibold tnum">{value}</dd>
    </div>
  );
}

function Children({ concept }: { concept: Concept }) {
  const [filter, setFilter] = useState("");
  const pages = useInfiniteQuery({
    queryKey: ["children", concept.id],
    queryFn: ({ pageParam }) => api.children(concept.id, pageParam, 100),
    initialPageParam: 0,
    getNextPageParam: (last, all) => { const n = all.reduce((s, p) => s + p.children.length, 0); return n < last.total ? n : undefined; },
    initialData: { pages: [{ total: concept.children_count, children: concept.children }], pageParams: [0] },
  });
  const all = pages.data?.pages.flatMap((p) => p.children) ?? [];
  const shown = filter ? all.filter((c) => c.name.toLowerCase().includes(filter.toLowerCase())) : all;
  if (concept.children_count === 0) return <p className="text-sm text-ink-3">None. This concept has no subtypes in the International view.</p>;
  return (
    <div>
      {concept.children_count > 8 && (
        <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={`Filter ${fmtInt(all.length)} loaded`}
          aria-label="Filter children" className="mb-2 h-9 w-full rounded-md border border-rule bg-surface px-3 text-sm outline-none focus:border-ink" />
      )}
      <ul className="max-h-[420px] overflow-auto rounded-lg border border-rule bg-surface">
        {shown.map((c) => (
          <li key={c.id} className="border-b border-rule last:border-b-0">
            <Link to={`/concept/${c.id}`} className="flex items-center justify-between gap-3 px-3 py-2 text-sm hover:bg-wash">
              <span className="min-w-0 truncate">{c.name}</span>
              <span className="sctid shrink-0">{c.id}</span>
            </Link>
          </li>
        ))}
        {shown.length === 0 && <li className="px-3 py-3 text-sm text-ink-3">No loaded child matches “{filter}”.</li>}
      </ul>
      {pages.hasNextPage && (
        <button type="button" onClick={() => pages.fetchNextPage()} disabled={pages.isFetchingNextPage} className="btn-quiet mt-2 w-full">
          {pages.isFetchingNextPage ? "Loading" : `Load more (${fmtInt(concept.children_count - all.length)} left)`}
        </button>
      )}
    </div>
  );
}

function Lineage({ id }: { id: string }) {
  const navigate = useNavigate();
  const [tall, setTall] = useState(false);
  const { data, isLoading } = useQuery({ queryKey: ["lineage", id], queryFn: () => api.lineage(id) });
  const graph = useMemo(() => {
    if (!data) return null;
    const nodes: GraphNode[] = data.nodes.map((n) => ({ id: n.id, name: n.name, tag: n.tag, variant: n.role }));
    const childIds = new Set(data.nodes.filter((n) => n.role === "child").map((n) => n.id));
    const edges: GraphEdge[] = data.edges.map((e) => ({ ...e, variant: childIds.has(e.child) ? "muted" : "plain" }));
    return { nodes, edges };
  }, [data]);
  return (
    <div className={`relative overflow-hidden rounded-xl border border-rule bg-[#F7F9FB] ${tall ? "fixed inset-3 z-40 shadow-pop" : ""}`}>
      <button type="button" onClick={() => setTall((t) => !t)} className="absolute right-3 top-3 z-10 rounded-md border border-rule bg-surface p-1.5 text-ink-3 hover:text-ink"
        aria-label={tall ? "Exit full screen" : "Show the graph full screen"}>
        {tall ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
      </button>
      {isLoading || !graph ? <Skeleton className="h-[640px] w-full rounded-none" /> : (
        <HierarchyGraph nodes={graph.nodes} edges={graph.edges} onOpen={(n) => navigate(`/concept/${n}`)}
          height={tall ? "calc(100vh - 60px)" : "min(72vh, 720px)"} fitKey={`${id}-${tall}`} />
      )}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-rule bg-surface px-3 py-2 text-xs text-ink-3">
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-ink" />This concept</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm border border-rule-strong bg-surface" />Ancestors</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm border border-dashed border-ink-4 bg-paper" />Children</span>
        {data && data.children_total > data.children_shown && (
          <span className="ml-auto">Showing {data.children_shown} of {fmtInt(data.children_total)} children</span>
        )}
      </div>
    </div>
  );
}

export default function ConceptPage() {
  const { id = "" } = useParams();
  const { data: c, error, isLoading } = useQuery({ queryKey: ["concept", id], queryFn: () => api.concept(id) });
  useTitle(c?.name ?? "Concept");

  if (error) {
    return (
      <div className="mx-auto max-w-xl px-4 py-16">
        <ErrorNote title={(error as ApiError).status === 404 ? `There is no concept ${id} in the International view.` : "This concept could not be loaded."}
          detail={(error as Error).message} />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1440px] px-4 pb-20 pt-6 sm:px-6">
      <div className="min-h-[20px]">{c && <Breadcrumb path={c.path} />}</div>
      <div className="mt-5 grid gap-8 lg:grid-cols-[400px_minmax(0,1fr)] xl:grid-cols-[440px_minmax(0,1fr)]">
        <article className="min-w-0">
          {isLoading || !c ? (
            <div className="space-y-3"><Skeleton className="h-9 w-3/4" /><Skeleton className="h-4 w-1/2" /><Skeleton className="mt-6 h-40 w-full" /></div>
          ) : (
            <div className="animate-rise space-y-8">
              <header>
                <h1 className="text-xl font-semibold sm:text-2xl">{c.name}</h1>
                <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                  {c.tag && <span className="rounded border border-rule bg-surface px-1.5 py-px text-xs text-ink-2">{c.tag}</span>}
                  <Sctid id={c.id} />
                </div>
                <p className="mt-3 text-sm text-ink-3">Fully specified name: <span className="text-ink-2">{c.fsn}</span></p>
              </header>

              <dl className="grid grid-cols-3 gap-4 border-y border-rule py-4">
                <Fact label="Depth" value={c.depth.shortest === c.depth.longest ? String(c.depth.shortest) : `${c.depth.shortest} to ${c.depth.longest}`}
                  hint="IS_A steps from the root, by the shortest and the longest route" />
                <Fact label="Ancestors" value={fmtInt(c.ancestors_count)} />
                <Fact label="Children" value={fmtInt(c.children_count)} />
              </dl>

              {c.synonyms.length > 0 && (
                <Section title="Synonyms">
                  <ul className="flex flex-wrap gap-1.5">
                    {c.synonyms.map((s) => <li key={s} className="rounded-md bg-wash px-2 py-0.5 text-sm text-ink-2">{s}</li>)}
                  </ul>
                </Section>
              )}

              {c.attributes.length > 0 && (
                <Section title="Defining attributes">
                  <dl className="divide-y divide-rule rounded-lg border border-rule bg-surface text-sm">
                    {c.attributes.map((a, i) => (
                      <div key={`${a.name}-${i}`} className="grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)] gap-3 px-3 py-2">
                        <dt className="text-ink-3">{a.name}</dt><dd className="text-ink-2">{a.value}</dd>
                      </div>
                    ))}
                  </dl>
                </Section>
              )}

              <Section title="Parents" aside={c.parents.length > 1 ? `${c.parents.length}, it sits in more than one branch` : undefined}>
                {c.parents.length === 0 ? <p className="text-sm text-ink-3">None. This is the root.</p> : (
                  <ul className="space-y-1.5">
                    {c.parents.map((p) => (
                      <li key={p.id} className="flex items-baseline justify-between gap-3 text-[14.5px]">
                        <ConceptLink c={p} /><span className="sctid shrink-0">{p.id}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </Section>

              <Section title="Children" aside={fmtInt(c.children_count)}><Children concept={c} /></Section>

              <div className="flex flex-wrap gap-2 border-t border-rule pt-6">
                <Link to={`/ask?task=T1&x=${c.id}`} className="btn-quiet"><MessageSquareText size={15} />Ask for its parents</Link>
                <Link to={`/ask?task=T5&x=${c.id}`} className="btn-quiet">Ask where it sits</Link>
              </div>
            </div>
          )}
        </article>
        <div className="min-w-0 lg:sticky lg:top-20 lg:self-start">
          <Lineage id={id} />
          <p className="mt-2 text-xs text-ink-3">Every ancestor up to the root, with all IS_A links between them. Click a concept to open it.</p>
        </div>
      </div>
    </div>
  );
}

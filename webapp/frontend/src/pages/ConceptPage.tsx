import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { ChevronRight, MessageSquareText, Maximize2, Minimize2 } from "lucide-react";
import { api, ApiError, type Concept, type Ref } from "../api/client";
import HierarchyGraph, { type GraphEdge, type GraphNode } from "../components/HierarchyGraph";
import { ErrorNote, Section, Sctid, Skeleton } from "../components/bits";
import { fmtInt } from "../utils/labels";
import { useDebounced, useMedia, useTitle } from "../utils/hooks";

function Breadcrumb({ path }: { path: Ref[] }) {
  const [open, setOpen] = useState(false);
  const long = path.length > 5 && !open;
  const shown: (Ref | null)[] = long ? [path[0], null, ...path.slice(-3)] : path;
  return (
    <nav aria-label="Path from the root" className="flex flex-wrap items-center gap-x-1 gap-y-1 text-sm text-ink-3">
      {shown.map((p, i) => (
        <span key={p ? p.id : "more"} className="flex min-w-0 items-center gap-1">
          {i > 0 && <ChevronRight size={13} className="shrink-0 text-ink-4" aria-hidden />}
          {p === null ? (
            <button type="button" onClick={() => setOpen(true)} className="rounded px-1 hover:bg-wash hover:text-ink"
              aria-label={`Show the ${path.length - 4} hidden steps`}>{path.length - 4} more</button>
          ) : i === shown.length - 1 ? <span className="truncate text-ink-2" aria-current="page">{p.name}</span>
            : <Link to={`/concept/${p.id}`} className="truncate hover:text-ink hover:underline" title={p.name}>{p.name}</Link>}
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
  const q = useDebounced(filter.trim(), 200);
  const pages = useInfiniteQuery({
    queryKey: ["children", concept.id, q],
    queryFn: ({ pageParam }) => api.children(concept.id, pageParam, 100, q),
    initialPageParam: 0,
    getNextPageParam: (last, all) => { const n = all.reduce((s, p) => s + p.children.length, 0); return n < last.total ? n : undefined; },
    initialData: q ? undefined : { pages: [{ total: concept.children_count, children: concept.children }], pageParams: [0] },
  });
  const all = pages.data?.pages.flatMap((p) => p.children) ?? [];
  const total = pages.data?.pages[0]?.total ?? 0;
  if (concept.children_count === 0) return <p className="text-sm text-ink-3">None. This concept has no subtypes in the International view.</p>;
  return (
    <div>
      {concept.children_count > 8 && (
        <div className="relative mb-2">
          <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={`Filter all ${fmtInt(concept.children_count)} children`}
            aria-label="Filter children" className="h-9 w-full rounded-md border border-rule bg-surface px-3 text-sm outline-none focus:border-ink" />
          {q && <span className="absolute right-3 top-2 text-xs text-ink-3 tnum">{pages.isFetching ? "" : `${fmtInt(total)} match`}</span>}
        </div>
      )}
      <ul className="max-h-[420px] overflow-auto rounded-lg border border-rule bg-surface" aria-busy={pages.isFetching}>
        {all.map((c) => (
          <li key={c.id} className="border-b border-rule last:border-b-0">
            <Link to={`/concept/${c.id}`} className="flex items-center justify-between gap-3 px-3 py-2 text-sm hover:bg-wash">
              <span className="min-w-0 truncate" title={c.name}>{c.name}</span>
              <span className="sctid shrink-0">{c.id}</span>
            </Link>
          </li>
        ))}
        {q && !pages.isFetching && all.length === 0 && <li className="px-3 py-3 text-sm text-ink-3">No child matches “{q}”.</li>}
        {pages.hasNextPage && (
          <li>
            <button type="button" onClick={() => pages.fetchNextPage()} disabled={pages.isFetchingNextPage}
              className="w-full px-3 py-2.5 text-left text-sm font-medium text-ink-2 hover:bg-wash">
              {pages.isFetchingNextPage ? "Loading" : `Show more (${fmtInt(total - all.length)} left)`}
            </button>
          </li>
        )}
      </ul>
    </div>
  );
}

const GRAPH_CHILDREN = 4;

function Lineage({ id }: { id: string }) {
  const navigate = useNavigate();
  const [tall, setTall] = useState(false);
  const wide = useMedia("(min-width: 1024px)");
  const { data, isLoading } = useQuery({ queryKey: ["lineage", id], queryFn: () => api.lineage(id) });
  const graph = useMemo(() => {
    if (!data) return null;
    const kids = data.nodes.filter((n) => n.role === "child");
    const shown = new Set(kids.slice(0, GRAPH_CHILDREN).map((n) => n.id));
    const nodes: GraphNode[] = data.nodes.filter((n) => n.role !== "child" || shown.has(n.id))
      .map((n) => ({ id: n.id, name: n.name, tag: n.tag, variant: n.role }));
    const edges: GraphEdge[] = data.edges.filter((e) => !kids.some((k) => k.id === e.child) || shown.has(e.child))
      .map((e) => ({ ...e, variant: shown.has(e.child) ? "muted" : "plain" }));
    const hidden = data.children_total - shown.size;
    if (hidden > 0) {
      nodes.push({ id: "__more", name: `${fmtInt(hidden)} more ${hidden === 1 ? "child" : "children"}`, variant: "more" });
      edges.push({ child: "__more", parent: id, variant: "muted" });
    }
    const parents = data.edges.filter((e) => e.child === id).map((e) => e.parent);
    return { nodes, edges, focus: [id, ...parents, ...shown, ...(hidden > 0 ? ["__more"] : [])] };
  }, [data, id]);
  const open = (n: string) => (n === "__more" ? document.getElementById("children")?.scrollIntoView({ behavior: "smooth" }) : navigate(`/concept/${n}`));
  return (
    <section aria-labelledby="lineage-h"
      className={`overflow-hidden rounded-xl border border-rule bg-surface ${tall ? "fixed inset-3 z-40 shadow-pop" : ""}`}>
      <div className="flex items-center justify-between gap-3 border-b border-rule px-4 py-2.5">
        <div className="flex min-w-0 items-baseline gap-3">
          <h2 id="lineage-h" className="text-sm font-semibold">Lineage</h2>
          <span className="hidden truncate text-xs text-ink-3 sm:inline">Every ancestor up to the root. Click a concept to open it.</span>
        </div>
        <button type="button" onClick={() => setTall((t) => !t)} className="shrink-0 rounded-md p-1.5 text-ink-3 hover:bg-wash hover:text-ink"
          aria-label={tall ? "Exit full screen" : "Show the graph full screen"}>
          {tall ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
        </button>
      </div>
      <div className="bg-[#F6F8FA]">
        {isLoading || !graph ? <Skeleton className="h-[560px] w-full rounded-none" /> : (
          <HierarchyGraph nodes={graph.nodes} edges={graph.edges} onOpen={open}
            height={tall ? "calc(100vh - 110px)" : wide ? "min(68vh, 680px)" : 420} fitKey={`${id}-${tall}`}
            focusIds={graph.focus} scrollPans={wide || tall} />
        )}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-rule px-4 py-2 text-xs text-ink-3">
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-[3px] bg-ink" />This concept</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-[3px] border border-[#C5CCD7] bg-surface" />Ancestor</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-[3px] border border-[#E1E5EB] bg-[#FAFBFC]" />Child</span>
        <span className="ml-auto tnum">{data ? `${fmtInt(data.nodes.filter((n) => n.role === "ancestor").length)} ancestors` : ""}</span>
      </div>
    </section>
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

  const depth = c ? (c.depth.shortest === c.depth.longest ? String(c.depth.shortest) : `${c.depth.shortest} to ${c.depth.longest}`) : "";
  return (
    <div className="pb-20">
      <header className="border-b border-rule bg-surface">
        <div className="mx-auto max-w-[1440px] px-4 pb-6 pt-5 sm:px-6">
          <div className="min-h-[20px]">{c && <Breadcrumb key={c.id} path={c.path} />}</div>
          {isLoading || !c ? (
            <div className="mt-4 space-y-3"><Skeleton className="h-9 w-2/5" /><Skeleton className="h-4 w-1/4" /></div>
          ) : (
            <div className="mt-4 flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
              <div className="min-w-0 max-w-[78ch]">
                <h1 className="text-xl font-semibold sm:text-2xl">{c.name}</h1>
                <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm text-ink-3">
                  {c.tag && <span className="rounded-md bg-wash px-2 py-0.5 text-xs font-medium text-ink-2">{c.tag}</span>}
                  <Sctid id={c.id} />
                  {c.hierarchy && c.hierarchy.id !== c.id && (
                    <span>in <Link to={`/concept/${c.hierarchy.id}`} className="text-ink-2 hover:text-ink hover:underline">{c.hierarchy.name}</Link></span>
                  )}
                </div>
              </div>
              <div className="flex shrink-0 flex-wrap items-center gap-6 lg:gap-8">
                <dl className="flex gap-6 lg:gap-8">
                  <Fact label="Depth" value={depth} hint="IS_A steps from the root, by the shortest and the longest route" />
                  <Fact label="Parents" value={fmtInt(c.parents.length)} />
                  <Fact label="Children" value={fmtInt(c.children_count)} />
                  <Fact label="Ancestors" value={fmtInt(c.ancestors_count)} />
                </dl>
                <div className="flex gap-2">
                  <Link to={`/ask?task=T1&x=${c.id}`} className="btn-primary"><MessageSquareText size={15} />Ask about it</Link>
                </div>
              </div>
            </div>
          )}
        </div>
      </header>

      <div className="mx-auto mt-6 grid max-w-[1440px] gap-6 px-4 sm:px-6 lg:grid-cols-[380px_minmax(0,1fr)] xl:grid-cols-[420px_minmax(0,1fr)]">
        <div className="min-w-0 lg:order-2 lg:sticky lg:top-20 lg:self-start"><Lineage id={id} /></div>
        <article className="min-w-0 lg:order-1">
          {isLoading || !c ? <Skeleton className="h-72 w-full" /> : (
            <div className="animate-rise space-y-7">
              <Section title="Fully specified name"><p className="text-[14.5px] text-ink-2">{c.fsn}</p></Section>

              {c.synonyms.length > 0 && (
                <Section title="Synonyms">
                  <ul className="flex flex-wrap gap-1.5">
                    {c.synonyms.map((s) => <li key={s} className="rounded-md border border-rule bg-surface px-2 py-0.5 text-sm text-ink-2">{s}</li>)}
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

              <Section title="Parents" aside={c.parents.length > 1 ? "in more than one branch" : undefined}>
                {c.parents.length === 0 ? <p className="text-sm text-ink-3">None. This is the root.</p> : (
                  <ul className="divide-y divide-rule rounded-lg border border-rule bg-surface">
                    {c.parents.map((p) => (
                      <li key={p.id}>
                        <Link to={`/concept/${p.id}`} className="flex items-center justify-between gap-3 px-3 py-2 text-sm hover:bg-wash">
                          <span className="min-w-0 truncate" title={p.name}>{p.name}</span><span className="sctid shrink-0">{p.id}</span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </Section>

              <div id="children" className="scroll-mt-24"><Section title="Children" aside={fmtInt(c.children_count)}><Children concept={c} /></Section></div>
            </div>
          )}
        </article>
      </div>
    </div>
  );
}

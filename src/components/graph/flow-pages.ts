import type { components } from "@/api/generated/schema";
type Page = components["schemas"]["FlowResponse"];
/** The API caps a page at 50. Collect pages without pretending one page is the whole graph. */
export async function collectFlowPages(
  load: (offset: number) => Promise<Page>,
  cap = 10000,
) {
  const nodes = new Map<string, Page["nodes"][number]>();
  const edges = new Map<string, Page["edges"][number]>();
  let offset = 0;
  let page: Page;
  do {
    page = await load(offset);
    for (const n of page.nodes) nodes.set(n.address, n);
    for (const e of page.edges) edges.set(e.id, e);
    offset += page.edges.length;
    if (!page.edges.length) break;
  } while (offset < (page.summary.total_flows ?? 0) && offset < cap);
  return {
    ...page,
    nodes: [...nodes.values()],
    edges: [...edges.values()],
    offset: 0,
    complete: edges.size >= (page.summary.total_flows ?? 0),
  };
}

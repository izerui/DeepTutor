/**
 * Let each block of rendered Markdown take its reading direction from its own
 * text (``dir="auto"``), so an Arabic paragraph sits right-aligned beside an
 * English one in the same reply. Code, math and diagrams are not blocks of
 * prose and stay left-to-right by inheriting the page's direction.
 */

type HastNode = {
  type: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
};

const TEXT_BLOCKS = new Set([
  "p",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "ul",
  "ol",
  "li",
  "blockquote",
  "th",
  "td",
]);

function mark(node: HastNode): void {
  if (node.type === "element" && node.tagName) {
    // Raw HTML in a reply may already say which way it reads; leave it be.
    if (TEXT_BLOCKS.has(node.tagName) && node.properties?.dir === undefined) {
      node.properties = { ...node.properties, dir: "auto" };
    }
    // A code block is left-to-right whatever it sits in.
    if (node.tagName === "pre") return;
  }
  node.children?.forEach(mark);
}

export function rehypeDirAuto() {
  return (tree: HastNode) => mark(tree);
}

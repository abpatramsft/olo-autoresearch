import { copyFileSync } from "node:fs";

const assets = {
  "marked.esm.js": "marked/lib/marked.esm.js",
  "purify.es.mjs": "dompurify/dist/purify.es.mjs",
  "file-text.svg": "lucide-static/icons/file-text.svg",
  "download.svg": "lucide-static/icons/download.svg",
  "x.svg": "lucide-static/icons/x.svg",
  "marked-LICENSE": "marked/LICENSE",
  "dompurify-LICENSE": "dompurify/LICENSE",
  "dompurify-LICENSE-MPL": "dompurify/LICENSE-MPL",
  "lucide-LICENSE": "lucide-static/LICENSE",
};

for (const [destination, source] of Object.entries(assets)) {
  copyFileSync(new URL(`node_modules/${source}`, import.meta.url), new URL(destination, import.meta.url));
}
console.log(`Synced ${Object.keys(assets).length} offline dashboard assets.`);
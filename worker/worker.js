/**
 * Incus simplestreams front door.
 *
 * Serves streams/v1 JSON from the GitHub Release and 302s image blobs
 * to Release assets. Do not proxy squashfs through this worker.
 *
 * Deploy: wrangler deploy --config worker/wrangler.toml
 * Optional env overrides: GITHUB_OWNER, GITHUB_REPO, RELEASE_TAG
 */

const GITHUB_OWNER = "LynMoe";
const GITHUB_REPO = "nyabase-images";
const GITHUB_REPO_URL = "https://github.com/LynMoe/nyabase-images";
const RELEASE_TAG = "stable";
const USER_AGENT = "nyabase-images-worker/1.0";

const IMAGE_FILE_RE =
  /^\/images\/([a-z0-9]+)\/([0-9.]+)\/([a-z0-9]+)\/([a-z0-9]+)\/([^/]+)\/(incus\.tar\.xz|rootfs\.squashfs)$/;

export default {
  async fetch(request, env, ctx) {
    return handle(request, env, ctx);
  },
};

async function handle(request, env, ctx) {
  const owner = env?.GITHUB_OWNER || GITHUB_OWNER;
  const repo = env?.GITHUB_REPO || GITHUB_REPO;
  const tag = env?.RELEASE_TAG || RELEASE_TAG;
  const repoUrl = env?.GITHUB_REPO_URL || GITHUB_REPO_URL;
  const url = new URL(request.url);
  const path = url.pathname.replace(/\/+$/, "") || "/";

  if (request.method === "OPTIONS") {
    return new Response(null, { headers: cors() });
  }
  if (request.method !== "GET" && request.method !== "HEAD") {
    return json({ error: "method not allowed" }, 405);
  }

  if (path === "/" || path === "/index.html") {
    return html(landing(repoUrl, tag, url.origin));
  }
  if (path === "/health" || path === "/api/health") {
    return json({ ok: true, repo: `${owner}/${repo}`, tag });
  }
  if (path === "/streams/v1/index.json") {
    return asset(request, owner, repo, tag, "index.json", ctx);
  }
  if (path === "/streams/v1/images.json") {
    return asset(request, owner, repo, tag, "images.json", ctx);
  }

  const match = path.match(IMAGE_FILE_RE);
  if (match) {
    const [, os, release, arch, variant, version, filename] = match;
    const name = `${os}-${release}-${arch}-${variant}-v${version}-${filename}`;
    const location = releaseDownload(owner, repo, tag, name);
    return new Response(null, {
      status: 302,
      headers: {
        ...cors(),
        Location: location,
        "Cache-Control": "public, max-age=60",
      },
    });
  }

  return json(
    {
      error: "not found",
      hint: "Incus remote is this origin with protocol=simplestreams",
    },
    404,
  );
}

function releaseDownload(owner, repo, tag, name) {
  return `https://github.com/${owner}/${repo}/releases/download/${tag}/${name}`;
}

async function asset(request, owner, repo, tag, name, ctx) {
  const cache = caches.default;
  const cacheKey = new Request(request.url, request);
  const cached = await cache.match(cacheKey);
  if (cached) {
    return cached;
  }

  const upstream = releaseDownload(owner, repo, tag, name);
  const res = await fetch(upstream, {
    headers: { "User-Agent": USER_AGENT, Accept: "application/json" },
    redirect: "follow",
  });
  if (!res.ok) {
    return json(
      {
        error: "catalog not published yet",
        status: res.status,
        asset: name,
        source: upstream,
      },
      res.status === 404 ? 503 : res.status,
    );
  }
  const body = await res.arrayBuffer();
  const out = new Response(body, {
    status: 200,
    headers: {
      ...cors(),
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "public, max-age=60",
    },
  });
  if (ctx && ctx.waitUntil) {
    ctx.waitUntil(cache.put(cacheKey, out.clone()));
  }
  return out;
}

function cors() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
    "Access-Control-Allow-Headers": "*",
  };
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body, null, 2) + "\n", {
    status,
    headers: { ...cors(), "Content-Type": "application/json; charset=utf-8" },
  });
}

function html(body) {
  return new Response(body, {
    headers: { ...cors(), "Content-Type": "text/html; charset=utf-8" },
  });
}

function landing(repoUrl, tag, origin) {
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>nyabase-images</title>
</head>
<body>
  <h1>nyabase-images</h1>
  <p>Incus simplestreams remote (JSON here, blobs 302 to GitHub Release <code>${tag}</code>).</p>
  <pre>incus remote add nyai ${origin} --protocol=simplestreams
incus image list nyai:
incus launch nyai:ubuntu/24.04 box</pre>
  <p><a href="${repoUrl}">${repoUrl}</a></p>
  <ul>
    <li><a href="/streams/v1/index.json">/streams/v1/index.json</a></li>
    <li><a href="/streams/v1/images.json">/streams/v1/images.json</a></li>
    <li><a href="/health">/health</a></li>
  </ul>
</body>
</html>`;
}

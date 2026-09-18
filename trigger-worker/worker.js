// Cloudflare Worker doc lap (KHONG phai Pages Function) dung lam relay cong khai
// cho nut "Cap nhat ngay" tren 2 site tinh (GitHub Pages) khong tu host duoc
// backend giu bi mat: bond-dashboard va c-bond-market-data. Ca hai la repo
// PUBLIC (GitHub Actions mien phi khong gioi han) nen KHONG can cooldown -
// khac voi fund-nav-dashboard (repo private, gioi han 4 tieng/lan).
//
// Token GITHUB_TRIGGER_TOKEN (Worker secret, khong bao gio lo ra client) can
// quyen "Actions: Read and write" tren CA HAI repo ben duoi.
const SITES = {
  "bond-dashboard": {
    owner: "thanhnhaxinhdep",
    repo: "bond-dashboard",
    workflow: "update.yml",
  },
  "c-bond-market-data": {
    owner: "thanhnhaxinhdep",
    repo: "c-bond-market-data",
    workflow: "weekly-scrape.yml",
  },
};

const ALLOWED_ORIGINS = new Set([
  "https://thanhnhaxinhdep.github.io",
]);

function corsHeaders(origin) {
  return {
    "Access-Control-Allow-Origin": ALLOWED_ORIGINS.has(origin) ? origin : "",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Vary": "Origin",
  };
}

function json(obj, status, origin) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...corsHeaders(origin), "Content-Type": "application/json" },
  });
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders(origin) });
    }
    if (request.method !== "POST") {
      return json({ ok: false, reason: "method_not_allowed" }, 405, origin);
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return json({ ok: false, reason: "bad_json" }, 400, origin);
    }

    const site = SITES[body.site];
    if (!site) {
      return json({ ok: false, reason: "unknown_site" }, 400, origin);
    }

    const token = env.GITHUB_TRIGGER_TOKEN;
    if (!token) {
      return json({ ok: false, reason: "server_misconfigured" }, 500, origin);
    }

    const resp = await fetch(
      `https://api.github.com/repos/${site.owner}/${site.repo}/actions/workflows/${site.workflow}/dispatches`,
      {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${token}`,
          "Accept": "application/vnd.github+json",
          "User-Agent": "bond-dashboard-trigger-worker",
          "X-GitHub-Api-Version": "2022-11-28",
        },
        body: JSON.stringify({ ref: "main" }),
      }
    );

    if (resp.status !== 204) {
      const detail = await resp.text().catch(() => "");
      return json({ ok: false, reason: "github_api_error", status: resp.status, detail }, 502, origin);
    }

    return json({ ok: true, triggered: true }, 200, origin);
  },
};

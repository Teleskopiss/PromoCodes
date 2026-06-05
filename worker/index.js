export default {
  async fetch(request, env) {
    // Always send CORS headers, including on errors
    const cors = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': '*',
      'Access-Control-Max-Age': '86400',
    };

    // Preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: cors });
    }

    if (request.method !== 'POST') {
      return new Response('Method not allowed', { status: 405, headers: cors });
    }

    const token = env.DISPATCH_PAT || env.GH_TOKEN;
    if (!token) {
      return new Response('No token configured on worker', { status: 500, headers: cors });
    }

    let ghStatus, ghBody;
    try {
      const resp = await fetch(
        'https://api.github.com/repos/Teleskopiss/PromoCodes/actions/workflows/scrape.yml/dispatches',
        {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${token}`,
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'Content-Type': 'application/json',
            'User-Agent': 'PromoRadar-Worker/1.0',
          },
          body: JSON.stringify({ ref: 'main' }),
        }
      );
      ghStatus = resp.status;
      ghBody = ghStatus === 204 ? 'ok' : await resp.text();
    } catch (err) {
      return new Response('GitHub fetch failed: ' + err.message, { status: 502, headers: cors });
    }

    return new Response(ghBody, {
      // Return 200 for GitHub's 204 (No Content = success)
      status: ghStatus === 204 ? 200 : ghStatus,
      headers: { ...cors, 'Content-Type': 'text/plain' },
    });
  },
};

export default {
  async fetch(request, env) {
    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    };

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders });
    }
    if (request.method !== 'POST') {
      return new Response('Method not allowed', { status: 405, headers: corsHeaders });
    }

    // Try both secret names so it works regardless of which one is set
    const token = env.DISPATCH_PAT || env.GH_TOKEN;
    if (!token) {
      return new Response('No token configured', { status: 500, headers: corsHeaders });
    }

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

    const body = resp.status === 204 ? 'ok' : await resp.text();
    return new Response(body, {
      status: resp.status === 204 ? 200 : resp.status,
      headers: { ...corsHeaders, 'Content-Type': 'text/plain' },
    });
  },
};

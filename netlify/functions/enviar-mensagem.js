exports.handler = async (event) => {
  const json = (statusCode, body) => ({
    statusCode,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  if (event.httpMethod !== 'POST') {
    return json(405, { error: 'Method Not Allowed' });
  }

  const token = process.env.META_WA_TOKEN;
  const phoneId = process.env.META_PHONE_ID || process.env.META_PHONE_NUMBER_ID;

  if (!token || !phoneId) {
    return json(500, {
      error: 'Variaveis META_WA_TOKEN e META_PHONE_ID/META_PHONE_NUMBER_ID nao configuradas no servidor.',
    });
  }

  let body;
  try {
    body = JSON.parse(event.body || '{}');
  } catch {
    return json(400, { error: 'Body invalido.' });
  }

  const { numero, texto } = body;
  if (!numero || !texto) {
    return json(400, { error: 'Campos numero e texto sao obrigatorios.' });
  }

  const resp = await fetch(`https://graph.facebook.com/v19.0/${phoneId}/messages`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      messaging_product: 'whatsapp',
      to: numero,
      type: 'text',
      text: { body: texto },
    }),
  });

  const data = await resp.json();
  return json(resp.status, data);
};

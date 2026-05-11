exports.handler = async (event) => {
  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, body: 'Method Not Allowed' };
  }

  const token   = process.env.META_WA_TOKEN;
  const phoneId = process.env.META_PHONE_ID;

  if (!token || !phoneId) {
    return {
      statusCode: 500,
      body: JSON.stringify({ error: 'Variáveis META_WA_TOKEN e META_PHONE_ID não configuradas no servidor.' }),
    };
  }

  let body;
  try {
    body = JSON.parse(event.body);
  } catch {
    return { statusCode: 400, body: JSON.stringify({ error: 'Body inválido.' }) };
  }

  const { numero, texto } = body;
  if (!numero || !texto) {
    return { statusCode: 400, body: JSON.stringify({ error: 'Campos numero e texto são obrigatórios.' }) };
  }

  const resp = await fetch(`https://graph.facebook.com/v19.0/${phoneId}/messages`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
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

  return {
    statusCode: resp.status,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  };
};

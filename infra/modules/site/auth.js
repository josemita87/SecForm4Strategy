// HTTP Basic auth on every request against the hash in the key-value store; no key fails closed (docs/site.md)
import cf from 'cloudfront';
import crypto from 'crypto';

const kvs = cf.kvs();

async function handler(event) {
  const request = event.request;
  const authorization = request.headers.authorization;
  const expected = await kvs.get('credential_sha256').catch(() => null);
  if (!expected || !authorization || crypto.createHash('sha256').update(authorization.value).digest('hex') !== expected) {
    return {
      statusCode: 401,
      statusDescription: 'Unauthorized',
      headers: { 'www-authenticate': { value: 'Basic realm="SecForm4Strategy"' } },
    };
  }
  // S3 has no directory index: /story/ and /story serve /story/index.html
  if (request.uri.endsWith('/')) {
    request.uri += 'index.html';
  } else if (!request.uri.split('/').pop().includes('.')) {
    request.uri += '/index.html';
  }
  return request;
}

import { beforeEach, describe, expect, it, vi } from 'vitest';

const kit = vi.hoisted(() => ({
  error: vi.fn((status: number, message: string) => {
    throw { kind: 'error', status, message };
  }),
  redirect: vi.fn((status: number, location: string) => {
    throw { kind: 'redirect', status, location };
  }),
}));

vi.mock('@sveltejs/kit', () => kit);
vi.mock('$lib/routes', () => ({ loginPath: () => '/login' }));

import { loadApi } from './load';

const rejectedApiCall = <T extends { status: number }>(status: number) =>
  Promise.reject(Object.assign(new Error(`HTTP ${status}`), { status })) as Promise<T>;

describe('loadApi', () => {
  beforeEach(() => {
    kit.error.mockClear();
    kit.redirect.mockClear();
  });

  it('returns successful API responses unchanged', async () => {
    const result = await loadApi(
      Promise.resolve({ status: 200 as const, data: 'ok' }),
      'Not found.',
    );

    expect(result).toEqual({ status: 200, data: 'ok' });
  });

  it('redirects forbidden workspace requests to the caller-supplied path', async () => {
    await expect(
      loadApi(rejectedApiCall(403), 'Not found.', { forbiddenRedirect: '/workspaces' }),
    ).rejects.toMatchObject({ kind: 'redirect', status: 307, location: '/workspaces' });

    expect(kit.redirect).toHaveBeenCalledWith(307, '/workspaces');
  });

  it('keeps the default forbidden mapping as not found', async () => {
    await expect(loadApi(rejectedApiCall(403), 'Not found.')).rejects.toMatchObject({
      kind: 'error',
      status: 404,
      message: 'Not found.',
    });
  });

  it('maps a configured bad request to its existing message', async () => {
    await expect(
      loadApi(rejectedApiCall(400), 'Not found.', { badRequest: 'Invalid path.' }),
    ).rejects.toMatchObject({ kind: 'error', status: 400, message: 'Invalid path.' });
  });

  it('keeps unauthenticated requests redirecting to login', async () => {
    await expect(loadApi(rejectedApiCall(401), 'Not found.')).rejects.toMatchObject({
      kind: 'redirect',
      status: 307,
      location: '/login',
    });
  });
});

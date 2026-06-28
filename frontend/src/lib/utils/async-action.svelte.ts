import { apiErrorMessage } from '$lib/api/mutate';

export function useAsyncAction() {
  let busy = $state(false);
  let error = $state('');

  async function run(fn: () => Promise<void>, fallback = 'Coś poszło nie tak'): Promise<void> {
    busy = true;
    error = '';
    try {
      await fn();
    } catch (e) {
      error = apiErrorMessage(e, fallback);
    } finally {
      busy = false;
    }
  }

  return {
    get busy() {
      return busy;
    },
    get error() {
      return error;
    },
    run,
    clearError: () => {
      error = '';
    },
  };
}

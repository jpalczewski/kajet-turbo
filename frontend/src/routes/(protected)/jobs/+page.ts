import type { PageLoad } from './$types';
import { loadApi } from '$lib/api/load';
import { apiListJobsApiMeJobsGet } from '$lib/api';

export const load: PageLoad = async () => {
  const res = await loadApi(apiListJobsApiMeJobsGet(), 'Błąd ładowania zadań.');
  return { jobs: res.data.jobs };
};

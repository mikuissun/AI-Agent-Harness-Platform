import { api, longRequest } from './client'
import type { Run } from './types'
export async function startRun(taskId: number) { return (await api.post<Run>(`/tasks/${taskId}/run`, undefined, longRequest)).data }
export async function getRun(id: string) { return (await api.get<Run>(`/runs/${encodeURIComponent(id)}`)).data }
export async function resumeRun(id: string) { return (await api.post<Run>(`/runs/${encodeURIComponent(id)}/resume`, undefined, longRequest)).data }

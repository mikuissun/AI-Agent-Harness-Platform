import { api } from './client'
import type { Task } from './types'
export async function listTasks() { return (await api.get<Task[]>('/tasks')).data }
export async function createTask(input: {title: string; instruction: string}) { return (await api.post<Task>('/tasks', input)).data }

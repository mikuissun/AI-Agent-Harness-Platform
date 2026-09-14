import axios from 'axios'

export const api = axios.create({ baseURL: '/api', timeout: 15000 })
// Run/approval requests wait for the existing synchronous backend. Never retry mutations.
export const longRequest = { timeout: 0 }
export async function runtimeInfo() { return (await api.get<{project_name: string; version: string; environment: string}>('/info')).data }
export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    if (error.response?.status === 404) return '未找到记录，请检查 ID 和当前连接的后端。'
    if (error.response?.status === 409) return '该操作已处理或暂时无法执行，请刷新状态后继续。'
    if (error.response?.status === 422) return '请检查任务标题和任务指令。'
    if (error.response?.status === 503) return '运行环境不可用，请检查后端配置。'
  }
  return '请求中断，执行结果可能尚未确定，请刷新后再提交其他操作。'
}

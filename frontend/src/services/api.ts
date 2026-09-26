// API 服务层
import { httpClient } from './http'
import type { 
  Task, User, Config,
  CreateTaskRequest, UpdateTaskRequest,
  CreateUserRequest, UpdateUserRequest,
  ApiResponse
} from '@/types'

export class ApiService {
  // 任务相关API
  async getTasks(): Promise<ApiResponse<{ tasks: Task[] }>> {
    return httpClient.get('/api/tasks')
  }

  async createTask(data: CreateTaskRequest): Promise<ApiResponse<Task>> {
    return httpClient.post('/api/task/add', data)
  }

  async updateTask(taskId: number, data: UpdateTaskRequest): Promise<ApiResponse<Task>> {
    return httpClient.post('/api/task/update', { task_id: taskId, ...data })
  }

  async deleteTask(taskId: number): Promise<ApiResponse<void>> {
    return httpClient.post('/api/task/delete', { task_id: taskId })
  }

  async executeTask(taskId: number): Promise<ApiResponse<any>> {
    return httpClient.post('/api/task/execute', { task_id: taskId })
  }

  async toggleTask(taskId: number): Promise<ApiResponse<any>> {
    return httpClient.post('/api/task/toggle', { task_id: taskId })
  }

  async getTaskHistory(taskId: number): Promise<ApiResponse<any>> {
    return httpClient.get(`/api/task/history/${taskId}`)
  }

  async listShareFolders(data: { url: string; pwd?: string; path?: string }): Promise<ApiResponse<any>> {
    return httpClient.post('/api/share/folders', data)
  }

  async getFilteredShareFiles(data: { url: string; pwd?: string; task_id: number }): Promise<ApiResponse<any>> {
    return httpClient.post('/api/share/filtered-files', data)
  }

  async listNetdiskFolders(data: { path?: string }): Promise<ApiResponse<any>> {
    return httpClient.post('/api/netdisk/folders', data)
  }

  async setTaskExcludes(taskId: number, files: string[]): Promise<ApiResponse<any>> {
    return httpClient.post('/api/task/exclude', { task_id: taskId, files })
  }

  async executeBatchTasks(taskIds: number[]): Promise<ApiResponse<any>> {
    return httpClient.post('/api/tasks/execute-all', { task_ids: taskIds })
  }

  async deleteBatchTasks(taskIds: number[]): Promise<ApiResponse<void>> {
    return httpClient.post('/api/tasks/batch-delete', { task_ids: taskIds })
  }

  async shareTask(taskId: number, options?: { password?: string, period?: number }): Promise<ApiResponse<any>> {
    return httpClient.post('/api/task/share', { task_id: taskId, ...options })
  }

  async getShareInfo(url: string, pwd?: string): Promise<ApiResponse<any>> {
    return httpClient.post('/api/share/info', { url, pwd })
  }

  // 移除parseShareUrl，使用现有的getShareInfo接口获取文件名

  async moveTask(taskId: number, newIndex: number): Promise<ApiResponse<void>> {
    return httpClient.post('/api/task/move', { task_id: taskId, new_index: newIndex })
  }

  // 用户相关API
  async getUsers(): Promise<ApiResponse<{ users: User[], current_user: string }>> {
    return httpClient.get('/api/users')
  }

  async createUser(data: CreateUserRequest): Promise<ApiResponse<User>> {
    return httpClient.post('/api/user/add', data)
  }

  async updateUser(data: UpdateUserRequest): Promise<ApiResponse<User>> {
    return httpClient.post('/api/user/update', data)
  }

  async switchUser(username: string): Promise<ApiResponse<any>> {
    return httpClient.post('/api/user/switch', { username })
  }

  async deleteUser(username: string): Promise<ApiResponse<void>> {
    return httpClient.post('/api/user/delete', { username })
  }

  async getUserQuota(): Promise<ApiResponse<any>> {
    return httpClient.get('/api/user/quota')
  }

  async getUserCookies(username: string): Promise<ApiResponse<{ cookies: string }>> {
    return httpClient.get(`/api/user/${username}/cookies`)
  }

  // 配置相关API
  async getConfig(): Promise<ApiResponse<{ config: Config }>> {
    return httpClient.get('/api/config')
  }

  async updateConfig(config: any): Promise<ApiResponse<void>> {
    return httpClient.post('/api/config/update', config)
  }

  async testNotify(): Promise<ApiResponse<void>> {
    return httpClient.post('/api/notify/test')
  }

  async addNotifyField(name: string, value: string): Promise<ApiResponse<void>> {
    return httpClient.post('/api/notify/fields', { name, value })
  }

  async deleteNotifyField(name: string): Promise<ApiResponse<void>> {
    return httpClient.delete('/api/notify/fields', { name })
  }

  async updateAuth(data: { username: string, password: string, old_password: string }): Promise<ApiResponse<void>> {
    return httpClient.post('/api/auth/update', data)
  }

  // 其他API
  async checkVersion(source?: string): Promise<ApiResponse<any>> {
    return httpClient.get('/api/version/check', { source })
  }

  async getTasksStatus(): Promise<ApiResponse<{ tasks: Task[] }>> {
    return httpClient.get('/api/tasks/status')
  }

  async getTaskStatus(taskId: number): Promise<ApiResponse<Task>> {
    return httpClient.get(`/api/tasks/${taskId}/status`)
  }

  async getTaskLog(taskId: number): Promise<ApiResponse<any>> {
    return httpClient.get(`/api/task/log/${taskId}`)
  }

  // QMediaSync 相关
  async getQmsConfig(): Promise<ApiResponse<any>> {
    return httpClient.get('/api/qms/config')
  }

  async saveQmsConfig(data: any): Promise<ApiResponse<any>> {
    return httpClient.post('/api/qms/config', data)
  }

  async revealQmsKey(): Promise<ApiResponse<any>> {
    return httpClient.post('/api/qms/reveal')
  }

  async testQms(data: any = {}): Promise<ApiResponse<any>> {
    return httpClient.post('/api/qms/test', data)
  }

  async getQmsPaths(data: any = {}): Promise<ApiResponse<any>> {
    return httpClient.post('/api/qms/paths', data)
  }

  async getQmsSyncPaths(data: any = {}): Promise<ApiResponse<any>> {
    return httpClient.post('/api/qms/sync-paths', data)
  }

  async startQms(data: any): Promise<ApiResponse<any>> {
    return httpClient.post('/api/qms/start', data)
  }

  async getQmsLinks(): Promise<ApiResponse<any>> {
    return httpClient.get('/api/qms/links')
  }

  async createQmsLink(data: any): Promise<ApiResponse<any>> {
    return httpClient.post('/api/qms/links', data)
  }

  async updateQmsLink(data: any): Promise<ApiResponse<any>> {
    return httpClient.post('/api/qms/links/update', data)
  }

  async triggerQmsLink(id: string): Promise<ApiResponse<any>> {
    return httpClient.post('/api/qms/links/trigger', { id })
  }

  async getQmsLogs(linkId: string, limit = 30): Promise<ApiResponse<any>> {
    return httpClient.get(`/api/qms/logs?link_id=${encodeURIComponent(linkId)}&limit=${limit}`)
  }

  async deleteQmsLink(id: string): Promise<ApiResponse<any>> {
    return httpClient.post('/api/qms/links/delete', { id })
  }

  async toggleQmsLink(id: string, enabled: boolean): Promise<ApiResponse<any>> {
    return httpClient.post('/api/qms/links/toggle', { id, enabled })
  }

  // 认证相关API
  async login(username: string, password: string): Promise<ApiResponse<any>> {
    return httpClient.post('/api/auth/login', { username, password })
  }

  async logout(): Promise<ApiResponse<any>> {
    return httpClient.post('/api/auth/logout')
  }

  async checkAuth(): Promise<ApiResponse<any>> {
    return httpClient.get('/api/auth/check')
  }
}

// 单例模式导出
export const apiService = new ApiService()

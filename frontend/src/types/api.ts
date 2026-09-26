// API 相关类型定义

export interface ApiResponse<T = any> {
  success: boolean
  message?: string
  data?: T
  [key: string]: any
}

// 任务相关类型
export interface Task {
  /** 是否启用（缺省视为启用），停用后不参与定时执行 */
  enabled?: boolean
  /** 对比路径：留空则与保存路径比对 */
  compare_path?: string
  /** 只转存这些文件夹（分享链接内的路径） */
  transfer_folders?: string[]
  /** 是否保留文件夹结构 */
  keep_folder?: boolean
  /** 是否包含子目录：选中的文件夹里，是否连子文件夹里的文件一起转存（默认是） */
  include_subdirs?: boolean
  /** 排除清单：这些分享内路径的文件转存时跳过 */
  exclude_files?: string[]
  order: number
  task_uid?: string
  name?: string
  url: string
  save_dir: string
  pwd?: string
  status: 'normal' | 'error' | 'running' | 'success' | 'failed' | 'completed' | 'skipped'
  message?: string
  progress?: number
  category?: string
  cron?: string
  regex_pattern?: string
  regex_replace?: string
  share_info?: ShareInfo
  created_at?: string
  updated_at?: string
  last_execute_time?: number
  transferred_files?: string[]
}

export interface ShareInfo {
  url: string
  password?: string
  expires_at?: string
}

export interface CreateTaskRequest {
  url: string
  save_dir: string
  pwd?: string
  name?: string
  category?: string
  cron?: string
  regex_pattern?: string
  regex_replace?: string
}

export interface UpdateTaskRequest {
  url?: string
  save_dir?: string
  pwd?: string
  name?: string
  category?: string
  cron?: string
  regex_pattern?: string
  regex_replace?: string
}

export interface TaskOperation {
  type: 'execute' | 'edit' | 'delete' | 'share'
  taskId: number
}

export interface BatchOperation {
  type: 'execute' | 'delete'
  taskIds: number[]
}

// 用户相关类型
export interface User {
  username: string
  is_current: boolean
  quota?: UserQuota
  cookies_valid?: boolean
  last_active?: string
}

export interface UserQuota {
  used: number
  total: number
  used_formatted: string
  total_formatted: string
  percent: number
}

export interface CreateUserRequest {
  username: string
  cookies: string
}

export interface UpdateUserRequest {
  original_username: string
  username: string
  cookies: string
}

// 配置相关类型
export interface Config {
  notifications: NotificationConfig
  scheduling: SchedulingConfig
  sharing: SharingConfig
  general: GeneralConfig
}

export interface NotificationConfig {
  enabled: boolean
  webhook_url?: string
  custom_fields?: Record<string, string>
}

export interface SchedulingConfig {
  enabled: boolean
  interval: number
  start_time?: string
  end_time?: string
}

export interface SharingConfig {
  enabled: boolean
  default_password: boolean
  default_period: number
}

export interface GeneralConfig {
  max_retries: number
  timeout: number
  concurrent_limit: number
}

// 日志相关类型
export interface LogEntry {
  timestamp: string
  level: 'INFO' | 'WARNING' | 'ERROR' | 'DEBUG'
  message: string
  module?: string
}

// 版本相关类型
export interface VersionInfo {
  current: string
  latest: string
  has_update: boolean
  update_url?: string
  release_notes?: string
}

<template>
  <el-dialog
    v-model="visible"
    title="任务执行监控"
    width="800px"
    :before-close="handleClose"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    class="task-runner-dialog"
  >
    <div class="task-runner-content">
      <!-- 任务基本信息 -->
      <div class="task-info-section">
        <h3 class="section-title">任务信息</h3>
        <div class="task-info-grid">
          <div class="info-item">
            <span class="label">任务名称:</span>
            <span class="value">{{ task?.name || '未命名任务' }}</span>
          </div>
          <div class="info-item">
            <span class="label">保存路径:</span>
            <span class="value">{{ task?.save_dir }}</span>
          </div>
          <div class="info-item">
            <span class="label">执行状态:</span>
            <el-tag :type="getStatusType(currentStatus)" size="default">
              {{ getStatusText(currentStatus) }}
            </el-tag>
          </div>
          <div class="info-item">
            <span class="label">开始时间:</span>
            <span class="value">{{ startTime }}</span>
          </div>
        </div>
      </div>

      <!-- 执行状态 -->
      <div class="status-section">
        <h3 class="section-title">执行状态</h3>
        <div class="status-progress">
          <div class="progress-info">
            <div class="current-message">
              {{ currentMessage || '等待执行...' }}
            </div>
            <div class="elapsed-time">
              执行时间: {{ elapsedTime }}
            </div>
          </div>
          
          <!-- 执行中的进度条 -->
          <div v-if="isRunning" class="progress-bar">
            <el-progress
              :percentage="100"
              :indeterminate="true"
              :duration="3"
              status="success"
            />
          </div>

          <!-- 结果信息 -->
          <div v-if="!isRunning && currentMessage" class="result-info">
            <el-alert
              :type="getAlertType(currentStatus)"
              :title="currentMessage"
              show-icon
              :closable="false"
            />
          </div>
        </div>
      </div>

      <!-- 排除文件列表 -->
      <div v-if="task && task.exclude_files && task.exclude_files.length > 0" class="files-section">
        <h3 class="section-title">排除文件 ({{ task.exclude_files.length }}，本次不转存)</h3>
        <div class="files-list">
          <div v-for="(file, index) in task.exclude_files" :key="'ex' + index" class="file-item">
            <el-icon class="file-icon"><Remove /></el-icon>
            <span class="file-name">{{ file }}</span>
          </div>
        </div>
      </div>

      <!-- 转存文件列表 -->
      <div v-if="transferredFiles && transferredFiles.length > 0" class="files-section">
        <h3 class="section-title">转存文件 ({{ transferredFiles.length }})</h3>
        <div class="files-list">
          <div
            v-for="(file, index) in transferredFiles"
            :key="index"
            class="file-item"
          >
            <el-icon class="file-icon"><Document /></el-icon>
            <span class="file-name">{{ file.name || file.path || file }}</span>
            <span v-if="file.size" class="file-size">{{ formatFileSize(file.size) }}</span>
          </div>
        </div>
      </div>

      <!-- 执行日志 -->
      <div class="logs-section">
        <h3 class="section-title">
          执行日志
          <el-button
            size="small"
            type="primary"
            text
            @click="refreshLogs"
            :loading="logsLoading"
          >
            刷新日志
          </el-button>
        </h3>
        <div class="logs-container" ref="logsContainer">
          <div v-if="logs.length === 0" class="no-logs">
            暂无日志信息
          </div>
          <div
            v-for="(log, index) in logs"
            :key="index"
            class="log-entry"
            :class="`log-${log.level.toLowerCase()}`"
          >
            <span class="log-time">{{ formatLogTime(log.timestamp) }}</span>
            <span class="log-level">{{ log.level }}</span>
            <span class="log-message">{{ log.message }}</span>
          </div>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="dialog-footer">
        <el-button
          v-if="isRunning"
          type="warning"
          @click="handleCancel"
          :loading="cancelling"
        >
          取消执行
        </el-button>
        <el-button @click="handleClose">
          {{ isRunning ? '最小化' : '关闭' }}
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch, onUnmounted, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { Document } from '@element-plus/icons-vue'
import type { Task } from '@/types'
import { getTaskStatusText, formatFileSize } from '@/utils/helpers'
import { apiService } from '@/services/api'
import { createTaskStream, type TaskLogEntry } from '@/services/taskStream'

interface Props {
  modelValue: boolean
  task: Task | null
  taskId: number
}

interface Emits {
  (e: 'update:modelValue', value: boolean): void
  (e: 'task-completed', task: Task): void
  (e: 'task-cancelled'): void
}

const props = defineProps<Props>()
const emit = defineEmits<Emits>()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value)
})

const currentStatus = ref(props.task?.status || 'normal')
const currentMessage = ref(props.task?.message || '')
const startTime = ref('')
const elapsedTime = ref('00:00')
const transferredFiles = ref<any[]>([])

const logs = ref<TaskLogEntry[]>([])
const logsLoading = ref(false)
const logsContainer = ref<HTMLElement>()

const cancelling = ref(false)
let pollTimer: ReturnType<typeof setTimeout> | null = null
let timeTimer: ReturnType<typeof setInterval> | null = null
let streamFallbackTimer: ReturnType<typeof setTimeout> | null = null
let streamController: { close: () => void } | null = null
let startTimestamp: Date | null = null
let pollSessionId = 0
let streamSessionId = 0
let logsRequestId = 0
let terminalDetectedAt: number | null = null
let lastTerminalLogCount = 0
let completionHandled = false
let streamConnected = false
let streamErrorCount = 0

const POLL_INTERVAL_MS = 800
const TERMINAL_SETTLE_MS = 1500
const TERMINAL_MESSAGES = ['转存成功', '没有新文件需要转存']
const STREAM_CONNECT_TIMEOUT_MS = 4000
const STREAM_MAX_ERRORS = 2

const isRunning = computed(() => {
  return !isTerminalTask({
    status: currentStatus.value,
    message: currentMessage.value
  } as Task)
})

const resetCompletionTracking = () => {
  terminalDetectedAt = null
  lastTerminalLogCount = 0
  completionHandled = false
}

const clearStreamFallbackTimer = () => {
  if (streamFallbackTimer) {
    clearTimeout(streamFallbackTimer)
    streamFallbackTimer = null
  }
}

const closeTaskStream = () => {
  clearStreamFallbackTimer()
  if (streamController) {
    streamController.close()
    streamController = null
  }
  streamSessionId += 1
  streamConnected = false
  streamErrorCount = 0
}

const scrollLogsToBottom = async () => {
  await nextTick()
  if (logsContainer.value) {
    logsContainer.value.scrollTop = logsContainer.value.scrollHeight
  }
}

const setLogs = async (newLogs: TaskLogEntry[], forceScroll = false) => {
  if (newLogs.length < logs.value.length) {
    return logs.value
  }

  const logCountChanged = newLogs.length !== logs.value.length
  logs.value = newLogs

  if (logCountChanged || forceScroll) {
    await scrollLogsToBottom()
  }

  return logs.value
}

const appendLog = async (logEntry: TaskLogEntry) => {
  const lastLog = logs.value.at(-1)
  if (
    lastLog
    && lastLog.timestamp === logEntry.timestamp
    && lastLog.level === logEntry.level
    && lastLog.message === logEntry.message
  ) {
    return logs.value
  }

  logs.value = [...logs.value, logEntry]
  await scrollLogsToBottom()
  return logs.value
}

const applyTaskState = (taskData: Partial<Task> | null | undefined) => {
  if (!taskData) return

  if (taskData.status) {
    currentStatus.value = taskData.status
  }
  currentMessage.value = taskData.message || ''

  if (taskData.transferred_files) {
    transferredFiles.value = taskData.transferred_files
  }
}

const isTerminalTask = (taskData: Task | null | undefined) => {
  if (!taskData) return false

  if (['success', 'error', 'failed', 'completed', 'skipped'].includes(taskData.status)) {
    return true
  }

  return taskData.status === 'normal'
    && TERMINAL_MESSAGES.some(message => taskData.message?.includes(message))
}

const scheduleNextPoll = (delay = POLL_INTERVAL_MS) => {
  if (!visible.value) return

  if (pollTimer) {
    clearTimeout(pollTimer)
  }

  pollTimer = setTimeout(() => {
    void pollTaskState()
  }, delay)
}

const getStatusType = (status: string) => {
  const typeMap: Record<string, string> = {
    normal: 'info',
    running: 'warning',
    success: 'success',
    completed: 'success',
    skipped: 'success',
    error: 'danger',
    failed: 'danger'
  }
  return typeMap[status] || 'info'
}

const getStatusText = (status: string) => {
  return getTaskStatusText(status)
}

const getAlertType = (status: string) => {
  const typeMap: Record<string, 'success' | 'warning' | 'info' | 'error'> = {
    success: 'success',
    completed: 'success',
    skipped: 'success',
    error: 'error',
    failed: 'error',
    running: 'warning',
    normal: 'info'
  }
  return typeMap[status] || 'info'
}

const formatLogTime = (timestamp: string) => {
  if (!timestamp) return ''

  if (/^\d{2}:\d{2}:\d{2}$/.test(timestamp)) {
    return timestamp
  }

  try {
    const date = new Date(timestamp)
    if (isNaN(date.getTime())) {
      return timestamp
    }
    return date.toLocaleTimeString()
  } catch {
    return timestamp
  }
}

const updateElapsedTime = () => {
  if (!startTimestamp) return

  const now = new Date()
  const diff = now.getTime() - startTimestamp.getTime()
  const minutes = Math.floor(diff / 60000)
  const seconds = Math.floor((diff % 60000) / 1000)
  elapsedTime.value = `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`
}

const refreshLogs = async ({ showLoading = false }: { showLoading?: boolean } = {}) => {
  const sessionId = pollSessionId
  const currentReqId = ++logsRequestId

  if (showLoading) {
    logsLoading.value = true
  }

  try {
    const response = await apiService.getTaskLog(props.taskId)

    if (sessionId !== pollSessionId || currentReqId !== logsRequestId) {
      return logs.value
    }

    if (response.success) {
      const newLogs = response.logs || response.data?.logs || []
      return await setLogs(newLogs, showLoading)
    }
  } catch (error) {
    console.error('获取日志失败:', error)
  } finally {
    if (showLoading && currentReqId === logsRequestId) {
      logsLoading.value = false
    }
  }

  return logs.value
}

const checkTaskStatus = async () => {
  if (!props.task) return null

  const sessionId = pollSessionId

  try {
    const response = await apiService.getTaskStatus(props.taskId)

    if (sessionId !== pollSessionId) {
      return null
    }

    if (response.success) {
      const taskData = response.status || response.data
      applyTaskState(taskData)
      return taskData
    }
  } catch (error) {
    console.error('检查任务状态失败:', error)
  }

  return null
}

const stopPolling = () => {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }

  pollSessionId += 1
  logsRequestId += 1
  logsLoading.value = false
}

const stopMonitoring = () => {
  stopPolling()
  closeTaskStream()

  if (timeTimer) {
    clearInterval(timeTimer)
    timeTimer = null
  }
}

const finalizeTask = async (taskData: Task | null, options: { refreshLatestLogs?: boolean } = {}) => {
  if (completionHandled || !taskData) {
    return
  }

  completionHandled = true

  if (options.refreshLatestLogs) {
    await refreshLogs()
  }

  stopMonitoring()
  emit('task-completed', taskData)

  if (taskData.status === 'success' || taskData.status === 'completed' || taskData.status === 'skipped') {
    ElMessage.success('任务执行完成')
  } else if (taskData.status === 'error' || taskData.status === 'failed') {
    ElMessage.error('任务执行失败')
  } else {
    ElMessage.success('任务执行完成')
  }
}

const pollTaskState = async () => {
  if (!visible.value || !props.task) {
    return
  }

  const taskData = await checkTaskStatus()
  const latestLogs = await refreshLogs()

  if (!visible.value) {
    return
  }

  if (isTerminalTask(taskData)) {
    if (terminalDetectedAt === null || latestLogs.length !== lastTerminalLogCount) {
      terminalDetectedAt = Date.now()
      lastTerminalLogCount = latestLogs.length
      scheduleNextPoll(300)
      return
    }

    if (Date.now() - terminalDetectedAt >= TERMINAL_SETTLE_MS) {
      await finalizeTask(taskData, { refreshLatestLogs: false })
      return
    }

    scheduleNextPoll(300)
    return
  }

  terminalDetectedAt = null
  lastTerminalLogCount = latestLogs.length
  scheduleNextPoll()
}

const startPollingMode = () => {
  stopPolling()
  void pollTaskState()
}

const fallbackToPolling = (sessionId: number) => {
  if (!visible.value || sessionId !== streamSessionId) {
    return
  }

  closeTaskStream()
  startPollingMode()
}

const startTaskStream = (taskUid?: string) => {
  if (!taskUid) {
    return false
  }

  closeTaskStream()
  const sessionId = ++streamSessionId
  streamConnected = false
  streamErrorCount = 0

  clearStreamFallbackTimer()
  streamFallbackTimer = setTimeout(() => {
    if (!streamConnected) {
      fallbackToPolling(sessionId)
    }
  }, STREAM_CONNECT_TIMEOUT_MS)

  streamController = createTaskStream(taskUid, {
    onConnected: () => {
      if (sessionId !== streamSessionId) return
      streamConnected = true
      streamErrorCount = 0
      clearStreamFallbackTimer()
    },
    onSnapshot: async (payload) => {
      if (sessionId !== streamSessionId) return
      applyTaskState(payload.task)
      await setLogs(payload.logs || [], true)
    },
    onLog: async (payload) => {
      if (sessionId !== streamSessionId) return
      await appendLog(payload)
    },
    onStatus: (payload) => {
      if (sessionId !== streamSessionId) return
      applyTaskState(payload)
    },
    onCompleted: async ({ task }) => {
      if (sessionId !== streamSessionId) return
      applyTaskState(task)
      await finalizeTask(task)
    },
    onError: (error) => {
      if (sessionId !== streamSessionId || !visible.value) return
      console.error('任务 SSE 连接异常:', error)
      streamErrorCount += 1
      if (!streamConnected || streamErrorCount >= STREAM_MAX_ERRORS) {
        fallbackToPolling(sessionId)
      }
    }
  })

  return true
}

const startMonitoring = () => {
  if (!props.task) return

  stopMonitoring()
  resetCompletionTracking()
  logs.value = []
  transferredFiles.value = []
  elapsedTime.value = '00:00'
  currentStatus.value = 'running'
  currentMessage.value = '任务正在执行中...'

  startTimestamp = new Date()
  startTime.value = startTimestamp.toLocaleString()
  timeTimer = setInterval(updateElapsedTime, 1000)

  if (!startTaskStream(props.task.task_uid)) {
    startPollingMode()
  }
}

const handleCancel = async () => {
  cancelling.value = true
  try {
    ElMessage.warning('任务取消功能暂未实现')
    emit('task-cancelled')
  } catch {
    ElMessage.error('取消任务失败')
  } finally {
    cancelling.value = false
  }
}

const handleClose = async () => {
  if (isRunning.value) {
    await refreshLogs({ showLoading: true })
  }
  stopMonitoring()
  visible.value = false
}

watch(() => props.modelValue, (newVal) => {
  if (newVal && props.task) {
    startMonitoring()
  } else {
    stopMonitoring()
  }
})

onUnmounted(() => {
  stopMonitoring()
})
</script>

<style scoped>
.task-runner-dialog {
  --el-dialog-margin-top: 5vh;
}

.task-runner-content {
  max-height: 70vh;
  overflow-y: auto;
  overflow-x: hidden;
}

.section-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 12px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.task-info-section {
  margin-bottom: 24px;
  padding: 16px;
  background: #f8f9fa;
  border-radius: 8px;
}

.task-info-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 12px;
}

.info-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.info-item .label {
  font-weight: 500;
  color: #606266;
  min-width: 80px;
}

.info-item .value {
  color: #303133;
  word-break: break-all;
}

.status-section {
  margin-bottom: 24px;
}

.status-progress {
  min-width: 0;
  padding: 16px;
  background: white;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
}

.progress-info {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  min-width: 0;
  margin-bottom: 12px;
}

.current-message {
  flex: 1;
  min-width: 0;
  font-size: 14px;
  color: #303133;
  font-weight: 500;
  overflow-wrap: anywhere;
}

.elapsed-time {
  flex-shrink: 0;
  font-size: 12px;
  color: #909399;
  font-family: monospace;
}

.progress-bar {
  margin-bottom: 12px;
}

.result-info {
  margin-top: 12px;
}

.files-section {
  margin-bottom: 24px;
}

.files-list {
  max-height: 200px;
  overflow-y: auto;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  background: white;
}

.file-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid #f5f7fa;
}

.file-item:last-child {
  border-bottom: none;
}

.file-icon {
  color: #409eff;
  flex-shrink: 0;
}

.file-name {
  flex: 1;
  font-size: 14px;
  color: #303133;
  word-break: break-all;
}

.file-size {
  font-size: 12px;
  color: #909399;
  flex-shrink: 0;
}

.logs-section {
  margin-bottom: 16px;
}

.logs-container {
  height: 200px;
  overflow-y: auto;
  overflow-x: hidden;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  background: #f8f9fa;
  font-family: 'Courier New', monospace;
  font-size: 12px;
  padding: 8px;
  box-sizing: border-box;
}

.no-logs {
  text-align: center;
  color: #909399;
  padding: 40px;
}

.log-entry {
  display: grid;
  grid-template-columns: 60px 50px minmax(0, 1fr);
  align-items: start;
  gap: 8px;
  width: 100%;
  padding: 2px 0;
  border-bottom: 1px solid #f0f0f0;
}

.log-entry:last-child {
  border-bottom: none;
}

.log-time {
  color: #909399;
  min-width: 60px;
  flex-shrink: 0;
}

.log-level {
  min-width: 50px;
  font-weight: bold;
  flex-shrink: 0;
}

.log-message {
  min-width: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.log-info .log-level {
  color: #409eff;
}

.log-warning .log-level {
  color: #e6a23c;
}

.log-error .log-level {
  color: #f56c6c;
}

.log-debug .log-level {
  color: #909399;
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

/* 响应式设计 */
@media (max-width: 768px) {
  .task-runner-dialog {
    --el-dialog-margin-top: 2vh;
  }
  
  :deep(.el-dialog) {
    width: 95vw !important;
    margin: 2vh auto !important;
  }
  
  .task-info-grid {
    grid-template-columns: 1fr;
  }
  
  .progress-info {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
  
  .logs-container {
    font-size: 11px;
  }
}
</style>

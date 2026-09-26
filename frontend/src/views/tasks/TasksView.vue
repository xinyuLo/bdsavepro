<template>
  <div class="tasks-view">
    <div class="page-header">
      <h1 class="page-title">任务管理</h1>
      <div class="header-actions">
        <el-button type="primary" @click="addTask">
          <el-icon><Plus /></el-icon>
          添加任务
        </el-button>
      </div>
    </div>

    <!-- 工具栏 -->
    <div class="toolbar">
      <div class="toolbar-left">
        <el-input
          v-model="searchQuery"
          placeholder="搜索任务..."
          clearable
          style="width: 300px"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
        
        <el-select v-model="statusFilter" placeholder="状态筛选" style="width: 120px">
          <el-option label="全部" value="all" />
          <el-option label="正常" value="normal" />
          <el-option label="运行中" value="running" />
          <el-option label="成功" value="success" />
          <el-option label="错误" value="error" />
        </el-select>
        
        <el-select v-model="categoryFilter" placeholder="分类筛选" style="width: 140px">
          <el-option label="全部分类" value="all" />
          <el-option 
            v-for="category in uniqueCategories" 
            :key="category" 
            :label="category" 
            :value="category" 
          />
        </el-select>
        
        <el-button @click="toggleSortOrder" :type="isReversed ? 'primary' : ''" size="default">
          <el-icon><Sort /></el-icon>
          {{ isReversed ? '倒序' : '正序' }}
        </el-button>
        
        <el-tooltip content="列设置" placement="top">
          <el-button @click="showColumnSettings = true" size="default">
            <el-icon><Setting /></el-icon>
          </el-button>
        </el-tooltip>
      </div>
      
      <div class="toolbar-right">
        <el-button-group v-if="selectedTasks.length > 0">
          <el-button @click="executeBatchTasks">
            <el-icon><VideoPlay /></el-icon>
            批量执行 ({{ selectedTasks.length }})
          </el-button>
          <el-button @click="deleteBatchTasks">
            <el-icon><Delete /></el-icon>
            批量删除
          </el-button>
        </el-button-group>
      </div>
    </div>

    <!-- 任务列表 -->
    <div class="task-list-container">
      <div v-if="!canDragSort" class="sort-tip">
        当前处于搜索、筛选或倒序状态，请切回正序并清空筛选后再拖拽排序。
      </div>

      <!-- 桌面端表格 -->
      <el-table
        ref="tableRef"
        v-loading="loading"
        :data="filteredTasks"
        stripe
        row-key="order"
        :key="tableKey"
        @selection-change="handleSelectionChange"
        class="desktop-table"
      >
        <el-table-column type="selection" class-name="col-selection" width="45" />
        
        <!-- 拖拽手柄列 -->
        <el-table-column label="排序" class-name="col-drag" align="center" width="60">
          <template #default>
            <div
              class="drag-handle"
              :class="{ 'dragging': isDragging, 'drag-disabled': !canDragSort }"
              :title="canDragSort ? '拖拽调整顺序' : '请先切回正序并清空筛选后再拖拽排序'"
            >
              <el-icon><DCaret /></el-icon>
            </div>
          </template>
        </el-table-column>
        
        <el-table-column prop="order" label="序号" class-name="col-order" v-if="columnVisible.order" width="70" />
        
        <el-table-column prop="name" label="任务名称" class-name="col-name" v-if="columnVisible.name" min-width="220">
          <template #default="{ row }">
            <div class="task-name">
              <a 
                :href="getFullSourceLink(row)" 
                target="_blank" 
                class="source-link"
                :title="`完整转存链接: ${getFullSourceLink(row)}`"
              >
                {{ row.name || '未命名任务' }}
                <el-icon class="link-icon"><Link /></el-icon>
              </a>
            </div>
          </template>
        </el-table-column>
        
        <el-table-column label="分享链接" class-name="col-share" v-if="columnVisible.shareLink" min-width="180">
          <template #default="{ row }">
            <div class="share-link-container">
              <template v-if="row.share_info">
                <a 
                  :href="getFullShareLink(row.share_info)" 
                  target="_blank"
                  class="share-link"
                  :title="`分享链接: ${getFullShareLink(row.share_info)}`"
                >
                  {{ getFullShareLink(row.share_info) }}
                  <el-icon class="copy-icon" @click.prevent="copyShareLink(row.share_info)">
                    <CopyDocument />
                  </el-icon>
                </a>
              </template>
              <span v-else class="no-share">未生成分享链接</span>
            </div>
          </template>
        </el-table-column>
        
        <el-table-column prop="save_dir" label="保存路径" class-name="col-savedir" v-if="columnVisible.saveDir" min-width="240">
          <template #default="{ row }">
            <div class="save-dir text-truncate" :title="row.save_dir">
              {{ row.save_dir }}
            </div>
          </template>
        </el-table-column>
        
        <el-table-column prop="category" label="分类" class-name="col-category" v-if="columnVisible.category" width="130">
          <template #default="{ row }">
            <div class="task-category">
              <el-tag v-if="row.category" size="small" type="primary">
                {{ row.category }}
              </el-tag>
              <span v-else class="text-muted">-</span>
            </div>
          </template>
        </el-table-column>
        
        <!-- 定时规则列 -->
        <el-table-column label="定时规则" class-name="col-cron" v-if="columnVisible.cron" width="150">
          <template #default="{ row }">
            <div class="cron-display">
              <el-tag v-if="row.cron" size="small" type="warning">
                {{ row.cron }}
              </el-tag>
              <span v-else class="text-muted">使用默认</span>
            </div>
          </template>
        </el-table-column>
        
        <el-table-column prop="message" label="消息" class-name="col-message" v-if="columnVisible.message" min-width="160">
          <template #default="{ row }">
            <div class="task-message text-truncate" :title="row.message">
              {{ row.message || '-' }}
            </div>
          </template>
        </el-table-column>
        
        <el-table-column prop="status" label="状态" class-name="col-status" v-if="columnVisible.status" width="100">
          <template #default="{ row }">
            <el-tag :type="getStatusType(row.status)" size="small">
              {{ getStatusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        
        <el-table-column label="启用" class-name="col-enabled" width="80" align="center">
          <template #default="{ row }">
            <el-switch
              :model-value="row.enabled !== false"
              @change="toggleTaskEnabled(row)"
            />
          </template>
        </el-table-column>
        
        <el-table-column label="操作" class-name="col-actions" width="170">
          <template #default="{ row }">
            <div class="action-buttons">
              <el-button-group size="small">
                <el-button 
                  type="primary" 
                  title="立即执行"
                  @click="executeTask(row.order - 1)"
                  :disabled="row.status === 'running' || row.enabled === false"
                >
                  <el-icon><VideoPlay /></el-icon>
                </el-button>
                
                <el-button title="编辑任务" @click="editTask(row)">
                  <el-icon><Edit /></el-icon>
                </el-button>
                
                <el-button title="创建分享链接" @click="shareTask(row.order - 1)">
                  <el-icon><Share /></el-icon>
                </el-button>
              </el-button-group>
              
              <el-button-group size="small">
                <el-button title="转存日志" @click="openTaskLog(row)">
                  <el-icon><Tickets /></el-icon>
                </el-button>
                
                <el-button title="排除文件" @click="openExcludeDialog(row)">
                  <el-icon><Remove /></el-icon>
                </el-button>
                
                <el-button 
                  type="danger" 
                  title="删除任务"
                  @click="deleteTask(row.order - 1)"
                >
                  <el-icon><Delete /></el-icon>
                </el-button>
              </el-button-group>
            </div>
          </template>
        </el-table-column>
      </el-table>
      
      <!-- 转存历史日志弹窗 -->
      <el-dialog
        v-model="logDialogVisible"
        :title="`转存日志 - ${logTaskName}`"
        width="780px"
        top="6vh"
        append-to-body
      >
        <el-empty v-if="logHistory.length === 0" description="暂无转存记录（点一次“立即执行”就会生成）" :image-size="80" />
        <div v-else class="run-history-list">
          <div v-for="(h, idx) in logHistory" :key="idx" class="run-card">
            <div class="run-card-head">
              <div class="run-card-headline">
                <el-tag :type="h.success ? 'success' : 'danger'" size="small" effect="light">
                  {{ h.success ? '成功' : '失败' }}
                </el-tag>
                <span class="run-card-time">{{ h.start_time || '-' }} → {{ h.end_time || '-' }}</span>
              </div>
              <el-button link type="primary" size="small" @click="showHistoryDetail(h)">详情</el-button>
            </div>
            <div class="run-card-stats">
              <span class="run-chip run-chip-ok">转存 {{ h.file_count || 0 }} 个</span>
              <span class="run-chip">分享 {{ h.total_count || 0 }} 个</span>
              <span class="run-chip">排除 {{ h.excluded_count || 0 }}</span>
              <span class="run-chip">正则未命中 {{ h.filtered_count || 0 }}</span>
              <span class="run-chip">MD5 跳过 {{ h.md5_skipped || 0 }}</span>
            </div>
            <div class="run-card-path" :title="h.save_dir || h.compare_dir || ''">
              <el-icon><Folder /></el-icon>
              <span>转存到：{{ h.save_dir || '（旧记录未存路径，新记录会显示）' }}</span>
            </div>
            <div v-if="h.message" class="run-card-message">{{ h.message }}</div>
          </div>
        </div>
      </el-dialog>

      <!-- 历史详情弹窗 -->
      <el-dialog
        v-model="historyDetailVisible"
        title="转存记录详情"
        width="820px"
        top="5vh"
        append-to-body
      >
        <div v-if="historyDetail" class="hd-content">
          <div class="hd-section hd-section-info">
            <h3 class="hd-section-title">执行信息</h3>
            <div class="hd-info-grid">
              <div class="hd-info-item">
                <span class="hd-label">开始时间</span>
                <span class="hd-value">{{ historyDetail.start_time || '-' }}</span>
              </div>
              <div class="hd-info-item">
                <span class="hd-label">结束时间</span>
                <span class="hd-value">{{ historyDetail.end_time || '-' }}</span>
              </div>
              <div class="hd-info-item">
                <span class="hd-label">耗时</span>
                <span class="hd-value">{{ historyDuration(historyDetail) }}</span>
              </div>
              <div class="hd-info-item">
                <span class="hd-label">执行结果</span>
                <span class="hd-value">
                  <el-tag :type="historyDetail.success ? 'success' : 'danger'" size="small" effect="light">
                    {{ historyDetail.success ? '成功' : '失败' }}
                  </el-tag>
                </span>
              </div>
              <div class="hd-info-item hd-span-2">
                <span class="hd-label">转存路径</span>
                <span class="hd-value hd-path">{{ historyDetail.save_dir || '（旧记录未存路径）' }}</span>
              </div>
              <div class="hd-info-item hd-span-2">
                <span class="hd-label">对比路径</span>
                <span class="hd-value hd-path">{{ historyDetail.compare_dir || historyDetail.save_dir || '（旧记录未存路径）' }}</span>
              </div>
              <div class="hd-info-item hd-span-2">
                <span class="hd-label">转存文件夹</span>
                <span class="hd-value hd-path">{{ historyFolderNames || '（未选择，按整条链接转存）' }}</span>
              </div>
              <div class="hd-info-item">
                <span class="hd-label">包含子目录</span>
                <span class="hd-value">{{ historySubdirsText }}</span>
              </div>
              <div class="hd-info-item">
                <span class="hd-label">保存文件夹</span>
                <span class="hd-value">{{ historyDetail.keep_folder ? '是（连文件夹一起存）' : '否（只存里面的内容）' }}</span>
              </div>
              <div class="hd-info-item hd-span-2">
                <span class="hd-label">文件过滤</span>
                <span class="hd-value hd-path">{{ historyDetail.regex_pattern || '（未设置正则，全部转存）' }}</span>
              </div>
            </div>
          </div>

          <div class="hd-section">
            <h3 class="hd-section-title">执行结果</h3>
            <div class="hd-result-box">
              <div class="hd-result-message">{{ historyDetail.message || '无消息' }}</div>
              <div class="hd-stat-row">
                <div class="hd-stat">
                  <div class="hd-stat-value">{{ historyDetail.total_count || 0 }}</div>
                  <div class="hd-stat-label">分享文件</div>
                </div>
                <div class="hd-stat">
                  <div class="hd-stat-value hd-warn">{{ historyDetail.excluded_count || 0 }}</div>
                  <div class="hd-stat-label">排除清单跳过</div>
                </div>
                <div class="hd-stat">
                  <div class="hd-stat-value">{{ historyDetail.filtered_count || 0 }}</div>
                  <div class="hd-stat-label">正则未命中</div>
                </div>
                <div class="hd-stat">
                  <div class="hd-stat-value">{{ historyDetail.md5_skipped || 0 }}</div>
                  <div class="hd-stat-label">MD5 命中跳过</div>
                </div>
                <div class="hd-stat">
                  <div class="hd-stat-value hd-ok">{{ historyDetail.file_count || 0 }}</div>
                  <div class="hd-stat-label">本次转存</div>
                </div>
              </div>
            </div>
          </div>

          <div class="hd-section">
            <h3 class="hd-section-title">
              正则过滤后的文件（{{ regexPassedFiles.length }}）
              <span v-if="regexMatchedExcludedCount > 0" class="hd-section-hint">
                其中 {{ regexMatchedExcludedCount }} 个在下面的排除清单里
              </span>
            </h3>
            <div v-if="regexPassedFiles.length" class="hd-files">
              <div v-for="(f, i) in regexPassedFiles" :key="'p' + i" class="hd-file-item">
                <el-icon class="hd-file-icon hd-icon-ok"><Document /></el-icon>
                <span class="hd-file-name">{{ f }}</span>
              </div>
            </div>
            <div v-else class="hd-empty">没有文件命中过滤正则（正则未命中 {{ historyDetail.filtered_count || 0 }} 个）</div>
          </div>

          <div v-if="historyDetail.excluded_files && historyDetail.excluded_files.length" class="hd-section">
            <h3 class="hd-section-title">排除文件（{{ historyDetail.excluded_files.length }}，本次不转存）</h3>
            <div class="hd-files">
              <div v-for="(f, i) in historyDetail.excluded_files" :key="'e' + i" class="hd-file-item">
                <el-icon class="hd-file-icon hd-icon-warn"><Remove /></el-icon>
                <span class="hd-file-name">{{ f }}</span>
              </div>
            </div>
          </div>

          <div class="hd-section">
            <h3 class="hd-section-title">本次实际转存（{{ historyDetail.file_count || 0 }}）</h3>
            <div v-if="historyDetail.transferred_files && historyDetail.transferred_files.length" class="hd-files">
              <div v-for="(f, i) in historyDetail.transferred_files" :key="'t' + i" class="hd-file-item">
                <el-icon class="hd-file-icon"><Document /></el-icon>
                <span class="hd-file-name">{{ typeof f === 'string' ? f : (f.path || f.name || '') }}</span>
              </div>
            </div>
            <div v-else class="hd-empty">本次没有文件被转存（被排除清单 / MD5 去重 / 同名已存在拦住）</div>
          </div>

          <div class="hd-section hd-logs-section">
            <h3 class="hd-section-title">执行日志（{{ historyLogEntries.length }} 行）</h3>
            <div class="hd-logs">
              <div v-if="historyLogEntries.length === 0" class="hd-no-logs">暂无日志</div>
              <div v-for="(log, i) in historyLogEntries" :key="i" class="hd-log-entry">
                <span class="hd-log-level" :class="'hd-level-' + String(log.level).toLowerCase()">{{ log.level }}</span>
                <span class="hd-log-message">{{ log.message }}</span>
              </div>
            </div>
          </div>
        </div>
      </el-dialog>
      
      <!-- 排除文件弹窗 -->
      <el-dialog
        v-model="excludeDialogVisible"
        title="排除文件（后续转存时跳过）"
        width="640px"
        append-to-body
      >
        <div v-if="excludeLoading" class="folder-loading">正在读取分享文件并按过滤规则筛选…</div>
        <template v-else>
          <div class="exclude-tip">
            <span>勾选 = 后续转存时跳过该文件（按分享内路径匹配；若你在网盘里改了名，需要重新勾选）</span>
            <el-button
              size="small"
              type="warning"
              plain
              :disabled="excludeCandidates.length === 0"
              @click="selectAllNoMd5"
            >
              一键勾选无MD5文件
            </el-button>
          </div>
          <el-empty v-if="excludeCandidates.length === 0" description="没有可转存的文件（可能已被正则全部过滤）" :image-size="70" />
          <el-checkbox-group v-else v-model="excludeSelection" class="exclude-list">
            <div v-for="f in excludeCandidates" :key="f.path" class="folder-item">
              <el-checkbox :label="f.path">
                {{ f.path }}
                <span class="exclude-size">（{{ formatExcludeSize(f.size) }}）</span>
              </el-checkbox>
            </div>
          </el-checkbox-group>
        </template>
        <template #footer>
          <el-button @click="excludeDialogVisible = false">取消</el-button>
          <el-button type="primary" :loading="excludeSaving" @click="saveExcludes">保存排除清单</el-button>
        </template>
      </el-dialog>
      
      <!-- 移动端卡片布局 -->
      <div class="mobile-cards" v-loading="loading">
        <div v-if="filteredTasks.length === 0" class="empty-state">
          <div class="empty-text">暂无任务</div>
        </div>
        <div 
          v-for="task in filteredTasks" 
          :key="task.order"
          class="task-card"
        >
          <div class="task-card-body">
            <!-- 卡片头部 -->
            <div class="card-header">
              <div class="task-info">
                <h4 class="task-name">
                  <a 
                    :href="getFullSourceLink(task)" 
                    target="_blank" 
                    class="source-link"
                  >
                    {{ task.name || '未命名任务' }}
                    <el-icon class="link-icon"><Link /></el-icon>
                  </a>
                </h4>
                <div class="task-order">#{{ task.order }}</div>
              </div>
              <el-tag :type="getStatusType(task.status)" size="default">
                {{ getStatusText(task.status) }}
              </el-tag>
            </div>
            
            <!-- 卡片内容 -->
            <div class="card-content">
              <div class="content-row">
                <span class="label">保存路径:</span>
                <span class="value">{{ task.save_dir }}</span>
              </div>
              
              <div class="content-row" v-if="task.category">
                <span class="label">分类:</span>
                <span class="value">{{ task.category }}</span>
              </div>
              
              <div class="content-row" v-if="task.message">
                <span class="label">消息:</span>
                <span class="value">{{ task.message }}</span>
              </div>
              
              <!-- 高级功能标签 -->
              <div class="content-row" v-if="task.cron || task.regex_pattern || task.regex_replace">
                <span class="label">高级功能:</span>
                <div class="advanced-tags">
                  <el-tag v-if="task.cron" size="small" type="warning">定时</el-tag>
                  <el-tag v-if="task.regex_pattern" size="small" type="info">过滤</el-tag>
                  <el-tag v-if="task.regex_replace" size="small" type="success">重命名</el-tag>
                </div>
              </div>
              
              <!-- 分享链接 -->
              <div class="content-row" v-if="task.share_info">
                <span class="label">分享链接:</span>
                <a 
                  :href="getFullShareLink(task.share_info)" 
                  target="_blank"
                  class="share-link"
                >
                  查看分享链接
                  <el-icon class="copy-icon" @click.prevent="copyShareLink(task.share_info)">
                    <CopyDocument />
                  </el-icon>
                </a>
              </div>
            </div>
          </div>
          
          <!-- 卡片操作按钮 -->
          <div class="card-actions">
            <button 
              class="action-btn action-btn-primary"
              @click="executeTask(task.order - 1)"
              :disabled="task.status === 'running'"
              :title="task.status === 'running' ? '执行中...' : '执行任务'"
            >
              <el-icon><VideoPlay /></el-icon>
            </button>
            
            <button 
              class="action-btn"
              @click="editTask(task)"
              title="编辑任务"
            >
              <el-icon><Edit /></el-icon>
            </button>
            
            <button 
              class="action-btn"
              @click="shareTask(task.order - 1)"
              title="分享任务"
            >
              <el-icon><Share /></el-icon>
            </button>
            
            <button 
              class="action-btn action-btn-danger"
              @click="deleteTask(task.order - 1)"
              title="删除任务"
            >
              <el-icon><Delete /></el-icon>
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 添加/编辑任务对话框 -->
    <AddTaskDialog
      v-model="showAddTaskDialog"
      :task="editingTask"
      @success="handleTaskSuccess"
      @update:modelValue="handleDialogClose"
    />

    <!-- 任务运行窗口 -->
    <TaskRunnerDialog
      v-model="showTaskRunner"
      :task="runningTask"
      :task-id="runningTaskId"
      @task-completed="handleTaskCompleted"
      @task-cancelled="handleTaskCancelled"
      @update:modelValue="handleTaskRunnerClose"
    />
    
    <!-- 列设置对话框 -->
    <el-dialog
      v-model="showColumnSettings"
      title="列设置"
      width="400px"
    >
      <div class="column-settings">
        <el-checkbox v-model="columnVisible.order">序号</el-checkbox>
        <el-checkbox v-model="columnVisible.name">任务名称</el-checkbox>
        <el-checkbox v-model="columnVisible.shareLink">分享链接</el-checkbox>
        <el-checkbox v-model="columnVisible.saveDir">保存路径</el-checkbox>
        <el-checkbox v-model="columnVisible.category">分类</el-checkbox>
        <el-checkbox v-model="columnVisible.status">状态</el-checkbox>
        <el-checkbox v-model="columnVisible.message">消息</el-checkbox>
        <el-checkbox v-model="columnVisible.cron">定时规则</el-checkbox>
      </div>
      <template #footer>
        <el-button @click="showColumnSettings = false">取消</el-button>
        <el-button type="primary" @click="saveColumnSettings">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { ElMessage, ElMessageBox, type TableInstance } from 'element-plus'
import { 
  Plus, Search, VideoPlay, Delete, Edit, Share, 
  Link, CopyDocument, DCaret, Sort, Setting, Document, Remove, Folder
} from '@element-plus/icons-vue'
import { storeToRefs } from 'pinia'
import { useTaskStore } from '@/stores/tasks'
import { useTasks } from '@/composables/useTasks'
import AddTaskDialog from '@/components/business/AddTaskDialog.vue'
import TaskRunnerDialog from '@/components/business/TaskRunnerDialog.vue'
import type { Task } from '@/types'
import { apiService } from '@/services/api'
import { getTaskStatusText } from '@/utils/helpers'
import Sortable from 'sortablejs'

const taskStore = useTaskStore()
const { tasks, loading } = storeToRefs(taskStore)
const { 
  executeTask: executeTaskWithPolling, 
  executeBatchTasks: executeBatchTasksWithPolling,
  deleteTask: deleteTaskWithConfirm,
  deleteBatchTasks: deleteBatchTasksWithConfirm,
  shareTask: shareTaskWithOptions,
  moveTask,
  initTasks,
  fetchTasks
} = useTasks()

// 表格引用
const tableRef = ref<TableInstance>()

// 搜索和筛选
const searchQuery = ref('')
const statusFilter = ref('all')
const categoryFilter = ref('all')
// 从localStorage加载排序状态
const isReversed = ref(localStorage.getItem('taskListSortOrder') === 'desc')
const selectedTasks = ref<Task[]>([])

// 列可见性配置（从localStorage加载）
const defaultColumnVisible = {
  order: true,
  name: true,
  shareLink: true,
  saveDir: true,
  category: true,
  status: true,
  message: true,
  cron: true
}
const columnVisible = ref(
  JSON.parse(localStorage.getItem('taskListColumnVisible') || JSON.stringify(defaultColumnVisible))
)

// 对话框相关
const showAddTaskDialog = ref(false)
const showColumnSettings = ref(false)
const editingTask = ref<Task | null>(null)

// 任务运行窗口相关
const showTaskRunner = ref(false)
const runningTask = ref<Task | null>(null)
const runningTaskId = ref(-1)

// 拖拽状态
const isDragging = ref(false)
let sortableInstance: Sortable | null = null

// 强制重新渲染的key
const tableKey = ref(0)

// 计算属性
const uniqueCategories = computed(() => {
  const categories = tasks.value
    .map(task => task.category)
    .filter(category => category && category.trim())
  return [...new Set(categories)]
})

const filteredTasks = computed(() => {
  let result = tasks.value

  // 搜索筛选（任务名称、保存路径、分类）
  if (searchQuery.value) {
    const query = searchQuery.value.toLowerCase()
    result = result.filter(task => {
      const nameMatch = task.name?.toLowerCase().includes(query)
      const dirMatch = task.save_dir.toLowerCase().includes(query)
      const categoryMatch = task.category?.toLowerCase().includes(query)
      return nameMatch || dirMatch || categoryMatch
    })
  }

  // 状态筛选
  if (statusFilter.value !== 'all') {
    result = result.filter(task => task.status === statusFilter.value)
  }
  
  // 分类筛选
  if (categoryFilter.value !== 'all') {
    result = result.filter(task => task.category === categoryFilter.value)
  }
  
  // 排序（基于order字段）
  result = result.slice().sort((a, b) => {
    const orderA = a.order || 0
    const orderB = b.order || 0
    return isReversed.value ? orderB - orderA : orderA - orderB
  })

  return result
})

const canDragSort = computed(() => {
  return !searchQuery.value && statusFilter.value === 'all' && categoryFilter.value === 'all' && !isReversed.value
})

// 方法

const toggleSortOrder = () => {
  isReversed.value = !isReversed.value
  // 保存到localStorage
  localStorage.setItem('taskListSortOrder', isReversed.value ? 'desc' : 'asc')
  // 排序切换后强制重新渲染，确保排序立即生效
  tableKey.value++
}

// 保存列可见性配置
const saveColumnSettings = () => {
  localStorage.setItem('taskListColumnVisible', JSON.stringify(columnVisible.value))
  showColumnSettings.value = false
  ElMessage.success('列设置已保存')
}

const handleSelectionChange = (selection: Task[]) => {
  selectedTasks.value = selection
}

// 修改: 表格渲染完成后强制重算布局，避免刷新页面时列宽算错（列是逐个挂载的）
const relayoutTable = () => {
  nextTick(() => {
    try {
      tableRef.value?.doLayout()
    } catch {
      // 表格还没就绪时忽略
    }
  })
}

onMounted(() => {
  relayoutTable()
  window.addEventListener('resize', relayoutTable)
})

onUnmounted(() => {
  window.removeEventListener('resize', relayoutTable)
})

watch(() => tasks.value?.length, relayoutTable)
watch(columnVisible, relayoutTable, { deep: true })
watch(() => loading.value, relayoutTable)

// 排除文件
const excludeDialogVisible = ref(false)
const excludeLoading = ref(false)
const excludeSaving = ref(false)
const excludeCandidates = ref<{ path: string; size: number; md5?: string }[]>([])
const excludeSelection = ref<string[]>([])
let excludeTask: Task | null = null

const formatExcludeSize = (size?: number) => {
  if (!size || size <= 0) return '未知大小'
  const units = ['B', 'KB', 'MB', 'GB']
  let v = size
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`
}

const selectAllNoMd5 = async () => {
  const noMd5 = excludeCandidates.value
    .filter((f) => !f.md5)
    .map((f) => f.path)
  if (noMd5.length === 0) {
    ElMessage.info('所有文件都有 MD5，无需勾选')
    return
  }
  try {
    await ElMessageBox.confirm(
      `共 ${noMd5.length} 个文件没有 MD5 信息，确定全部勾选为排除？`,
      '一键勾选无MD5文件',
      { confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  const merged = new Set(excludeSelection.value)
  noMd5.forEach((p) => merged.add(p))
  excludeSelection.value = Array.from(merged)
  ElMessage.success(`已勾选 ${noMd5.length} 个无MD5文件`)
}

const historyDetailVisible = ref(false)
const historyDetail = ref<any>(null)

const showHistoryDetail = (h: any) => {
  historyDetail.value = h
  historyDetailVisible.value = true
}

const regexPassedFiles = computed(() => {
  if (!historyDetail.value) return []
  const matched = historyDetail.value.regex_matched_files
  if (Array.isArray(matched)) return matched
  const legacy = historyDetail.value.regex_passed_files
  return Array.isArray(legacy) ? legacy : []
})

// 规则命中的文件里，有多少被排除清单拦下了
const regexMatchedExcludedCount = computed(() => {
  const excl = new Set(
    ((historyDetail.value && historyDetail.value.excluded_files) || []).map((f: string) =>
      String(f).split('/').filter(Boolean).pop() || f
    )
  )
  return regexPassedFiles.value.filter((f: string) => {
    const short = String(f).split('/').filter(Boolean).pop() || f
    return excl.has(short)
  }).length
})

const historyFolderNames = computed(() => {
  const list = historyDetail.value && historyDetail.value.transfer_folders
  if (!Array.isArray(list) || list.length === 0) return ''
  return list
    .map((p: string) => String(p).split('/').filter(Boolean).pop() || p)
    .join('、')
})

const historySubdirsText = computed(() => {
  const v = historyDetail.value ? historyDetail.value.include_subdirs : undefined
  if (v === undefined || v === null || v === '') return '是（默认）'
  return v ? '是（连子文件夹一起存）' : '否（只存该文件夹下的文件）'
})

// 历史日志解析：兼容 "[INFO] xxx" 字符串与结构化对象
const historyLogEntries = computed(() => {
  const raw = (historyDetail.value && historyDetail.value.logs) || []
  return raw.map((line: any) => {
    if (line && typeof line === 'object') {
      return {
        level: line.level || 'INFO',
        message: line.message || '',
        time: line.timestamp || ''
      }
    }
    const text = String(line == null ? '' : line)
    const m = /^\[([A-Za-z]+)\]\s*(.*)$/.exec(text)
    return { level: m ? m[1] : 'INFO', message: m ? m[2] : text, time: '' }
  })
})

const historyDuration = (h: any) => {
  if (!h || !h.start_time || !h.end_time) return '-'
  const s = new Date(h.start_time.replace(/-/g, '/')).getTime()
  const e = new Date(h.end_time.replace(/-/g, '/')).getTime()
  if (!s || !e || e < s) return '-'
  const sec = Math.round((e - s) / 1000)
  if (sec < 60) return `${sec} 秒`
  return `${Math.floor(sec / 60)} 分 ${sec % 60} 秒`
}

const openExcludeDialog = async (task: Task) => {
  excludeTask = task
  excludeDialogVisible.value = true
  excludeLoading.value = true
  excludeCandidates.value = []
  excludeSelection.value = []
  try {
    const pwdMatch = /(?:[?&])pwd=([^&]+)/.exec(task.url || '')
    const res = await apiService.getFilteredShareFiles({
      url: task.url,
      pwd: pwdMatch ? pwdMatch[1] : (task.pwd || ''),
      task_id: task.order - 1
    })
    if (res.success) {
      excludeCandidates.value = ((res as any).files || []) as { path: string; size: number }[]
      const existing = ((res as any).excluded || []) as string[]
      const candPaths = new Set(excludeCandidates.value.map((f) => f.path))
      excludeSelection.value = existing.filter((p) => candPaths.has(p))
    } else {
      ElMessage.error((res as any).message || '读取分享文件失败')
      excludeDialogVisible.value = false
    }
  } catch {
    ElMessage.error('读取分享文件失败（链接可能已失效）')
    excludeDialogVisible.value = false
  } finally {
    excludeLoading.value = false
  }
}

const saveExcludes = async () => {
  if (!excludeTask) return
  excludeSaving.value = true
  try {
    const existing = (excludeTask.exclude_files || []) as string[]
    const candPaths = new Set(excludeCandidates.value.map((f) => f.path))
    const selected = new Set(excludeSelection.value)
    // 保留不在当前候选列表里的旧排除项，再合并新勾选
    const merged = Array.from(new Set([...existing.filter((p) => !candPaths.has(p)), ...selected]))
    const res = await apiService.setTaskExcludes(excludeTask.order - 1, merged)
    if (res.success) {
      ElMessage.success((res as any).message || '已保存')
      excludeDialogVisible.value = false
      await fetchTasks()
    } else {
      ElMessage.error((res as any).message || '保存失败')
    }
  } catch {
    ElMessage.error('保存失败')
  } finally {
    excludeSaving.value = false
  }
}

const toggleTaskEnabled = async (task: Task) => {
  try {
    const res = await apiService.toggleTask(task.order - 1)
    if (res.success) {
      ElMessage.success(res.message || '操作成功')
      await fetchTasks()
    } else {
      ElMessage.error(res.message || '操作失败')
    }
  } catch {
    ElMessage.error('操作失败')
  }
}

// 转存历史日志
const logDialogVisible = ref(false)
const logTaskName = ref('')
const logHistory = ref<any[]>([])

const historyStatusText = (s: string) => {
  const map: Record<string, string> = {
    success: '转存成功',
    failed: '转存失败',
    error: '转存失败',
    skipped: '无新文件'
  }
  return map[s] || s
}

const openTaskLog = async (task: Task) => {
  logTaskName.value = task.name || '未命名任务'
  logDialogVisible.value = true
  logHistory.value = []
  try {
    const res = await apiService.getTaskHistory(task.order - 1)
    logHistory.value = ((res as any).history || []) as any[]
  } catch {
    ElMessage.error('获取转存日志失败')
  }
}

const executeTask = async (taskId: number) => {
  // 找到对应的任务
  const task = tasks.value.find(t => t.order - 1 === taskId)
  if (!task) {
    ElMessage.error('任务不存在')
    return
  }
  
  // 检查任务是否正在运行
  if (task.status === 'running') {
    ElMessage.warning('任务正在执行中')
    return
  }
  
  // 设置运行窗口数据并显示
  runningTask.value = task
  runningTaskId.value = taskId
  showTaskRunner.value = true
  
  // 执行任务（不等待完成）
  executeTaskWithPolling(taskId).catch((error) => {
    console.error('任务执行失败:', error)
  })
}

const executeBatchTasks = async () => {
  const taskIds = selectedTasks.value.map(task => task.order - 1)
  await executeBatchTasksWithPolling(taskIds)
  // 批量执行后强制重新渲染，确保任务状态更新立即显示
  tableKey.value++
}

const deleteTask = async (taskId: number) => {
  await deleteTaskWithConfirm(taskId)
  // 删除后强制重新渲染，确保任务立即从列表中消失
  tableKey.value++
}

const deleteBatchTasks = async () => {
  const taskIds = selectedTasks.value.map(task => task.order - 1)
  await deleteBatchTasksWithConfirm(taskIds)
  // 批量删除后强制重新渲染，确保删除的任务立即从列表中消失
  tableKey.value++
  // 清空选择状态
  selectedTasks.value = []
}

const shareTask = async (taskId: number) => {
  try {
    const shareInfo = await shareTaskWithOptions(taskId)
    
    ElMessageBox.alert(
      `分享链接：\n${getFullShareLink(shareInfo)}`,
      '分享信息',
      {
        confirmButtonText: '复制链接',
        callback: async () => {
          try {
            await navigator.clipboard.writeText(getFullShareLink(shareInfo))
            ElMessage.success('链接已复制到剪贴板')
          } catch {
            ElMessage.warning('复制失败，请手动复制')
          }
        }
      }
    )
  } catch (error) {
    // 错误已在composable中处理
  }
}

const addTask = () => {
  editingTask.value = null
  showAddTaskDialog.value = true
}

const editTask = (task: Task) => {
  editingTask.value = task
  showAddTaskDialog.value = true
}

const handleTaskSuccess = () => {
  editingTask.value = null
  // 强制重新渲染任务列表，确保新增/编辑的任务立即显示
  tableKey.value++
}

const handleDialogClose = (visible: boolean) => {
  // 当对话框关闭时，确保清除编辑状态
  if (!visible) {
    editingTask.value = null
  }
}

// 任务运行窗口相关处理
const handleTaskCompleted = () => {
  // 任务完成后更新任务列表
  taskStore.fetchTasks()
}

const handleTaskCancelled = () => {
  // 任务取消处理
  ElMessage.info('任务已取消')
}

const handleTaskRunnerClose = () => {
  // 清理运行窗口状态
  runningTask.value = null
  runningTaskId.value = -1
}

// 拖拽功能
const initSortable = async () => {
  await nextTick()

  // 销毁之前的实例，避免状态切换后仍然可拖拽
  if (sortableInstance) {
    sortableInstance.destroy()
    sortableInstance = null
  }

  if (!canDragSort.value) {
    return
  }

  const el = tableRef.value?.$el?.querySelector('.el-table__body-wrapper tbody')
  if (!el) return

  sortableInstance = Sortable.create(el, {
    animation: 300,
    ghostClass: 'sortable-ghost',
    chosenClass: 'sortable-chosen',
    dragClass: 'sortable-drag',
    handle: '.drag-handle', // 只有拖拽手柄可以拖拽
    onStart: () => {
      isDragging.value = true
    },
    onEnd: async (event) => {
      isDragging.value = false

      if (!canDragSort.value) {
        ElMessage.warning('请切回正序并清空筛选后再拖拽排序')
        await fetchTasks()
        tableKey.value++
        return
      }

      const { oldIndex, newIndex } = event
      if (oldIndex === undefined || newIndex === undefined || oldIndex === newIndex) {
        return
      }

      try {
        const movedTask = filteredTasks.value[oldIndex]
        if (movedTask) {
          // 仅在完整正序列表中允许拖拽，order 可直接映射到后端 task_id
          const taskIndex = movedTask.order - 1
          if (taskIndex !== -1) {
            // 调用API移动任务
            moveTask(taskIndex, newIndex).then(async () => {
              console.log('任务顺序已同步到后端')

              // 重新获取最新数据
              await fetchTasks()

              // 强制重新渲染整个表格（这会销毁并重新创建所有DOM）
              tableKey.value++

              console.log('拖拽排序完成，表格已强制重新渲染')

            }).catch(async (error) => {
              console.error('API调用失败:', error)
              // 失败时也重新获取数据恢复状态
              await fetchTasks()
              tableKey.value++
            })
          }
        }
      } catch (error) {
        // 错误已在moveTask中处理
        console.error('任务排序失败:', error)
        // 发生错误时恢复状态
        fetchTasks()
        tableKey.value++
      }
    }
  })
}

// 转存链接处理方法
const getFullSourceLink = (task: any) => {
  if (!task.url) return ''
  
  // 如果URL已经包含密码参数，直接返回
  if (task.url.includes('?pwd=') || task.url.includes('&pwd=')) {
    return task.url
  }
  
  // 如果任务中有密码信息，添加到URL中
  // 根据config.json的数据结构，密码存储在pwd字段中
  if (task.pwd && task.pwd.trim() !== '') {
    const separator = task.url.includes('?') ? '&' : '?'
    return `${task.url}${separator}pwd=${task.pwd}`
  }
  
  return task.url
}

// 分享链接处理方法
const getFullShareLink = (shareInfo: any) => {
  if (!shareInfo) return ''
  
  const baseUrl = shareInfo.url
  if (shareInfo.password) {
    // 如果URL已包含密码参数，直接返回
    if (baseUrl.includes('?pwd=') || baseUrl.includes('&pwd=')) {
      return baseUrl
    }
    // 添加密码参数
    const separator = baseUrl.includes('?') ? '&' : '?'
    return `${baseUrl}${separator}pwd=${shareInfo.password}`
  }
  
  return baseUrl
}

const copyShareLink = async (shareInfo: any) => {
  const fullLink = getFullShareLink(shareInfo)
  if (fullLink) {
    try {
      await navigator.clipboard.writeText(fullLink)
      ElMessage.success('分享链接已复制到剪贴板')
    } catch (error) {
      ElMessage.warning('复制失败，请手动复制')
    }
  }
}

const getStatusType = (status: string) => {
  const typeMap: Record<string, string> = {
    normal: 'info',
    running: 'warning', 
    success: 'success',
    error: 'danger'
  }
  return typeMap[status] || 'info'
}

const getStatusText = (status: string) => {
  return getTaskStatusText(status)
}

// 全局添加任务事件处理
const handleGlobalAddTask = () => {
  addTask() // 调用现有的添加任务方法
}

onMounted(async () => {
  await initTasks()
  // 初始化拖拽功能
  await initSortable()
  
  // 监听全局添加任务事件
  window.addEventListener('global-add-task', handleGlobalAddTask)
})

onUnmounted(() => {
  // 清理全局事件监听器
  window.removeEventListener('global-add-task', handleGlobalAddTask)
})

// 监听tasks变化，重新初始化拖拽
watch(tasks, async () => {
  if (tasks.value.length > 0) {
    await nextTick()
    await initSortable()
  }
}, { deep: true })

watch([searchQuery, statusFilter, categoryFilter, isReversed], async () => {
  await nextTick()
  await initSortable()
})
</script>

<style scoped>
.tasks-view {
  padding: 24px;
  min-height: 100vh;
  background-color: #f5f5f5;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}

.page-title {
  font-size: 24px;
  font-weight: 600;
  color: #333;
  margin: 0;
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  padding: 16px;
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.toolbar-left {
  display: flex;
  gap: 16px;
  align-items: center;
}

.toolbar-right {
  display: flex;
  gap: 16px;
  align-items: center;
}

.task-list-container {
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  overflow: hidden;
}

.sort-tip {
  padding: 12px 16px;
  font-size: 13px;
  color: #e6a23c;
  background: #fdf6ec;
  border-bottom: 1px solid #faecd8;
}

.desktop-table {
  width: 100%;
}

/* 表格固定布局 */
.desktop-table :deep(.el-table__header),
.desktop-table :deep(.el-table__body) {
  table-layout: fixed;
  width: 100%;
}

/* 修改: 原先按 colgroup 第 N 列写死百分比宽度，刷新时列注册顺序变化会导致对号错位
   （任务名称被分到 3% 挤成竖排）。改为完全使用 el-table-column 的 width / min-width。 */

.task-name {
  font-weight: 500;
  color: #333;
}

.source-link {
  color: #409eff;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.source-link:hover {
  color: #66b1ff;
  text-decoration: underline;
}


.link-icon {
  font-size: 12px;
  opacity: 0.7;
}

.share-link-container {
  font-family: monospace;
  font-size: 12px;
}

.share-link {
  color: #67c23a;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.share-link:hover {
  color: #85ce61;
  text-decoration: underline;
}

.copy-icon {
  font-size: 16px;
  opacity: 0.7;
  cursor: pointer;
  flex-shrink: 0;
  padding: 2px;
}

.copy-icon:hover {
  opacity: 1;
  color: #409eff;
}

.no-share {
  color: #c0c4cc;
  font-style: italic;
}

.task-url {
  font-family: monospace;
  font-size: 12px;
  color: #666;
}

.save-dir {
  color: #666;
}

.task-message {
  color: #888;
  font-size: 12px;
}

.advanced-features {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.no-advanced {
  color: #c0c4cc;
  font-size: 12px;
}

/* 拖拽相关样式 */
.drag-handle {
  cursor: grab;
  color: #c0c4cc;
  transition: color 0.3s;
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 4px;
}

.drag-handle.drag-disabled {
  cursor: not-allowed;
  color: #dcdfe6;
}

.drag-handle:not(.drag-disabled):hover {
  color: #409eff;
}

.drag-handle:active,
.drag-handle.dragging {
  cursor: grabbing;
  color: #409eff;
}

/* Sortable样式 */
:deep(.sortable-ghost) {
  opacity: 0.4;
  background-color: #f0f9ff !important;
}

:deep(.sortable-chosen) {
  background-color: #e1f5fe !important;
}

:deep(.sortable-drag) {
  opacity: 0.8;
  transform: rotate(5deg);
  background-color: #ffffff !important;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15) !important;
}

/* 禁用拖拽时的行选择 */
:deep(.el-table__body-wrapper tbody) {
  user-select: none;
}

/* 移动端卡片样式 */
.mobile-cards {
  display: none;
}

.task-card {
  background: white;
  border-radius: 12px;
  border: 1px solid #e4e7ed;
  padding: 14px;
  margin-bottom: 10px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  border-left: 3px solid transparent;
  transition: all 0.2s ease;
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.task-card:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
  border-left-color: #409eff;
}

.task-card-body {
  flex: 1;
  min-width: 0;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
}

.task-info {
  flex: 1;
  min-width: 0;
}

.task-name {
  margin: 0 0 4px 0;
  font-size: 16px;
  font-weight: 600;
  line-height: 1.4;
}

.task-name .source-link {
  color: #409eff;
  text-decoration: none;
  display: flex;
  align-items: center;
  gap: 4px;
  word-break: break-all;
}

.task-name .source-link:hover {
  color: #66b1ff;
}

.task-order {
  font-size: 12px;
  color: #909399;
  font-weight: normal;
}

.card-content {
  margin-bottom: 16px;
}

.content-row {
  display: flex;
  align-items: flex-start;
  margin-bottom: 8px;
  font-size: 14px;
}

.content-row:last-child {
  margin-bottom: 0;
}

.content-row .label {
  color: #606266;
  font-weight: 500;
  min-width: 80px;
  flex-shrink: 0;
}

.content-row .value {
  color: #303133;
  word-break: break-all;
  flex: 1;
}

.advanced-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.card-actions {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: center;
  justify-content: flex-start;
  min-width: 44px;
  width: 44px;
  flex-shrink: 0;
}

.action-btn {
  width: 36px;
  height: 36px;
  padding: 8px;
  border-radius: 8px;
  font-size: 16px;
  transition: all 0.2s ease;
  touch-action: manipulation;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f5f7fa;
  border: 1px solid #e4e7ed;
  color: #606266;
  margin: 0;
  cursor: pointer;
}

.action-btn:hover:not(:disabled) {
  transform: scale(0.95);
  background: #409eff;
  color: white;
  border-color: #409eff;
}

.action-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  background: #f5f7fa;
  color: #c0c4cc;
}

.action-btn-primary {
  color: #409eff;
  border-color: rgba(64, 158, 255, 0.3);
}

.action-btn-primary:hover:not(:disabled) {
  background: #409eff;
  color: white;
}

.action-btn-danger {
  color: #f56c6c;
  border-color: rgba(245, 108, 108, 0.3);
}

.action-btn-danger:hover:not(:disabled) {
  background: #f56c6c;
  border-color: #f56c6c;
  color: white;
}

.empty-state {
  text-align: center;
  padding: 40px 20px;
  color: #909399;
}

.empty-text {
  font-size: 16px;
}

/* 列设置对话框 */
.column-settings {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.column-settings .el-checkbox {
  margin: 0;
  height: 40px;
  display: flex;
  align-items: center;
}

/* 响应式设计 - 统一断点为1200px */
@media (max-width: 1200px) {
  .tasks-view {
    padding: 16px;
  }
  
  .page-header {
    flex-direction: column;
    gap: 16px;
    align-items: flex-start;
  }
  
  .toolbar {
    flex-direction: column;
    gap: 16px;
    align-items: stretch;
  }
  
  .toolbar-left {
    flex-direction: column;
    align-items: stretch;
  }
  
  .toolbar-left .el-input,
  .toolbar-left .el-select {
    width: 100% !important;
  }
  
  /* 移动端隐藏表格，显示卡片 */
  .desktop-table {
    display: none !important;
  }
  
  .mobile-cards {
    display: block !important;
  }
  
  /* 移动端隐藏拖拽列 */
  .drag-handle {
    display: none;
  }
  
  /* 优化移动端操作按钮 */
  .card-actions .el-button {
    min-height: 44px;
    font-size: 14px;
  }
  
  /* 移动端表头按钮优化 */
  .header-actions .el-button {
    min-height: 44px;
    padding: 12px 16px;
  }
  
  /* 移动端工具栏按钮优化 */
  .toolbar .el-button {
    min-height: 44px;
    padding: 12px 16px;
  }
  
  /* 移动端复制按钮优化 */
  .copy-icon {
    font-size: 18px;
    padding: 8px;
    opacity: 0.8;
    min-width: 44px;
    min-height: 44px;
    display: flex;
    align-items: center;
    justify-content: center;
  }
}
/* 转存历史日志 */
.log-status {
  font-weight: 500;
  margin-bottom: 4px;
}

.log-message {
  color: #606266;
  font-size: 13px;
}

.log-files {
  margin: 6px 0 0;
  padding-left: 18px;
  color: #909399;
  font-size: 12px;
}

.log-files li {
  margin-top: 2px;
  word-break: break-all;
}

/* ===== 转存日志列表（卡片式） ===== */
.run-history-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-height: 68vh;
  overflow-y: auto;
}

.run-card {
  padding: 14px 16px;
  background: #f8f9fa;
  border: 1px solid #e4e7ed;
  border-radius: 10px;
}

.run-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.run-card-headline {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex-wrap: wrap;
}

.run-card-time {
  font-size: 13px;
  color: #606266;
  font-family: 'Courier New', monospace;
}

.run-card-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}

.run-chip {
  font-size: 12px;
  color: #606266;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 12px;
  padding: 2px 10px;
}

.run-chip-ok {
  color: #67c23a;
  border-color: #c2e7b0;
  background: #f0f9eb;
}

.run-card-path {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #409eff;
  word-break: break-all;
}

.run-card-message {
  margin-top: 8px;
  font-size: 13px;
  color: #303133;
}

/* ===== 详情弹窗（对齐执行监控风格） ===== */
.hd-content {
  max-height: 70vh;
  overflow-y: auto;
  overflow-x: hidden;
}

.hd-section {
  margin-bottom: 22px;
}

.hd-section-info {
  padding: 16px;
  background: #f8f9fa;
  border-radius: 8px;
}

.hd-section-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  margin: 0 0 12px;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.hd-section-hint {
  font-size: 12px;
  font-weight: 400;
  color: #e6a23c;
  background: #fdf6ec;
  border: 1px solid #f5dab1;
  border-radius: 10px;
  padding: 1px 8px;
}

.hd-info-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
}

.hd-info-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
}

.hd-span-2 {
  grid-column: 1 / -1;
}

.hd-label {
  font-weight: 500;
  color: #606266;
  min-width: 70px;
  flex-shrink: 0;
}

.hd-value {
  color: #303133;
  word-break: break-all;
}

.hd-path {
  font-family: 'Courier New', monospace;
  font-size: 13px;
  color: #409eff;
}

.hd-result-box {
  padding: 16px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
}

.hd-result-message {
  font-size: 14px;
  color: #303133;
  font-weight: 500;
  margin-bottom: 14px;
  word-break: break-all;
}

.hd-stat-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(96px, 1fr));
  gap: 10px;
}

.hd-stat {
  text-align: center;
  padding: 10px 6px;
  background: #f8f9fa;
  border-radius: 8px;
}

.hd-stat-value {
  font-size: 20px;
  font-weight: 600;
  color: #303133;
  line-height: 1.2;
}

.hd-stat-value.hd-warn {
  color: #e6a23c;
}

.hd-stat-value.hd-ok {
  color: #67c23a;
}

.hd-stat-label {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
}

.hd-files {
  max-height: 200px;
  overflow-y: auto;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  background: #fff;
}

.hd-file-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid #f5f7fa;
}

.hd-file-item:last-child {
  border-bottom: none;
}

.hd-file-icon {
  color: #409eff;
  flex-shrink: 0;
}

.hd-file-icon.hd-icon-warn {
  color: #e6a23c;
}

.hd-file-icon.hd-icon-ok {
  color: #67c23a;
}

.hd-file-name {
  flex: 1;
  font-size: 14px;
  color: #303133;
  word-break: break-all;
}

.hd-empty {
  padding: 18px;
  text-align: center;
  font-size: 13px;
  color: #909399;
  background: #f8f9fa;
  border-radius: 8px;
}

.hd-logs {
  height: 220px;
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

.hd-no-logs {
  text-align: center;
  color: #909399;
  padding: 40px 0;
}

.hd-log-entry {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 3px 4px;
  border-radius: 4px;
}

.hd-log-entry:hover {
  background: #eef1f6;
}

.hd-log-level {
  flex-shrink: 0;
  min-width: 48px;
  font-weight: 600;
  color: #909399;
}

.hd-level-error, .hd-level-failed {
  color: #f56c6c;
}

.hd-level-warning, .hd-level-warn {
  color: #e6a23c;
}

.hd-level-success {
  color: #67c23a;
}

.hd-level-info {
  color: #409eff;
}

.hd-log-message {
  flex: 1;
  color: #303133;
  word-break: break-all;
}
/* 操作列按钮两行排布 */
.action-buttons {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}
/* 转存历史列表 */
.history-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.history-item {
  padding: 10px 14px;
  background: #f5f7fa;
  border-radius: 8px;
}

.history-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.history-time {
  font-size: 13px;
  color: #606266;
}

.history-count {
  font-size: 13px;
  color: #303133;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px 16px;
  font-size: 13px;
  color: #303133;
  margin-bottom: 10px;
}

.detail-block {
  margin-top: 12px;
}

.detail-block h4 {
  margin: 0 0 6px;
  font-size: 13px;
  color: #606266;
}

.detail-logs {
  max-height: 260px;
  overflow-y: auto;
  background: #f5f7fa;
  padding: 10px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
}

/* 排除文件 */
.exclude-tip {
  margin-bottom: 10px;
  padding: 8px 12px;
  background: #fdf6ec;
  color: #e6a23c;
  font-size: 13px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.exclude-list {
  max-height: 380px;
  overflow-y: auto;
}

.exclude-size {
  color: #909399;
  font-size: 12px;
}
</style>

'use client'

import { useState, useEffect, useCallback, useMemo } from 'react'
import { motion } from 'framer-motion'
import { cn } from '@/lib/utils'
import { apiClient } from '@/lib/api-client'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { CardWatermark } from '@/components/ui/card-watermark'
import { Icons } from '@/components/ui/icons'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog'
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/ui/select'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'

// Matches app/schemas/data_source.py's DataSource / DataSourceSummary
// exactly — this page is wired to the real backend
// (GET/POST/PATCH/DELETE /api/data-manager/sources,
// GET /api/data-manager/summary, POST .../check[-all]). No demo data.
interface DataSource {
  id: number
  name: string
  category: string
  system_type: string
  description: string | null
  endpoint_url: string | null
  config: string | null
  status: 'healthy' | 'degraded' | 'down' | 'unconfigured'
  last_checked_at: string | null
  last_success_at: string | null
  latency_ms: number | null
  error_message: string | null
  created_at: string
  updated_at: string | null
}

interface DataSourceSummary {
  total: number
  healthy: number
  degraded: number
  down: number
  unconfigured: number
  by_category: Record<string, number>
}

interface SourceFormState {
  name: string
  category: string
  system_type: string
  description: string
  endpoint_url: string
}

const EMPTY_FORM: SourceFormState = {
  name: '',
  category: 'channel',
  system_type: '',
  description: '',
  endpoint_url: '',
}

const CATEGORY_META: Record<string, { label: string; icon: React.ElementType; blurb: string }> = {
  channel: {
    label: 'Channels',
    icon: Icons.inbox,
    blurb: 'Where disruption notices arrive (email, Teams, Slack).',
  },
  system_of_record: {
    label: 'Systems of Record',
    icon: Icons.folder,
    blurb: 'Where orders and inventory actually live (Airtable, Supabase).',
  },
  human_loop: {
    label: 'Human Loop',
    icon: Icons.users,
    blurb: 'Where a person approves or resolves a Workbench item.',
  },
  agent_platform: {
    label: 'Agent Platform',
    icon: Icons.bot,
    blurb: 'The Auto Orchestrator and its Operators.',
  },
}

const CATEGORY_ORDER = ['channel', 'system_of_record', 'human_loop', 'agent_platform']

const STATUS_META: Record<
  DataSource['status'],
  { label: string; icon: React.ElementType; dot: string; text: string; bg: string }
> = {
  healthy: { label: 'Healthy', icon: Icons.checkCircle, dot: 'bg-emerald-500', text: 'text-emerald-700', bg: 'bg-emerald-50 border-emerald-200' },
  degraded: { label: 'Degraded', icon: Icons.alertTriangle, dot: 'bg-amber-500', text: 'text-amber-700', bg: 'bg-amber-50 border-amber-200' },
  down: { label: 'Down', icon: Icons.alertCircle, dot: 'bg-red-500', text: 'text-red-700', bg: 'bg-red-50 border-red-200' },
  unconfigured: { label: 'Unconfigured', icon: Icons.helpCircle, dot: 'bg-gray-400', text: 'text-gray-600', bg: 'bg-gray-50 border-gray-200' },
}

const SYSTEM_TYPE_SUGGESTIONS = ['auto', 'slack', 'supabase', 'airtable', 'email', 'teams']

const containerVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.1 } },
}

const itemVariants = {
  hidden: { opacity: 0, y: 20 },
  visible: { opacity: 1, y: 0 },
}

function StatusBadge({ status }: { status: DataSource['status'] }) {
  const meta = STATUS_META[status]
  const Icon = meta.icon
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium',
        meta.bg,
        meta.text
      )}
    >
      <span className={cn('h-1.5 w-1.5 rounded-full', meta.dot)} />
      <Icon className="h-3.5 w-3.5" strokeWidth={1.5} />
      {meta.label}
    </span>
  )
}

function timeAgo(iso: string | null): string {
  if (!iso) return 'never'
  const diffMs = Date.now() - new Date(iso).getTime()
  const mins = Math.round(diffMs / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}

export default function DataManagerPage() {
  const [sources, setSources] = useState<DataSource[]>([])
  const [summary, setSummary] = useState<DataSourceSummary | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [checkingAll, setCheckingAll] = useState(false)
  const [checkingId, setCheckingId] = useState<number | null>(null)

  const [formOpen, setFormOpen] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState<SourceFormState>(EMPTY_FORM)
  const [formError, setFormError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const [deleteTarget, setDeleteTarget] = useState<DataSource | null>(null)
  const [deleting, setDeleting] = useState(false)

  const fetchAll = useCallback(async () => {
    setIsLoading(true)
    setLoadError(null)
    try {
      const [sourcesData, summaryData] = await Promise.all([
        apiClient.get<DataSource[]>('/api/data-manager/sources'),
        apiClient.get<DataSourceSummary>('/api/data-manager/summary'),
      ])
      setSources(sourcesData)
      setSummary(summaryData)
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Failed to load data sources.')
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchAll()
  }, [fetchAll])

  const grouped = useMemo(() => {
    const map: Record<string, DataSource[]> = {}
    for (const s of sources) {
      if (!map[s.category]) map[s.category] = []
      map[s.category].push(s)
    }
    return map
  }, [sources])

  const extraCategories = useMemo(
    () => Object.keys(grouped).filter((c) => !CATEGORY_ORDER.includes(c)),
    [grouped]
  )

  const openCreateForm = () => {
    setEditingId(null)
    setForm(EMPTY_FORM)
    setFormError(null)
    setFormOpen(true)
  }

  const openEditForm = (source: DataSource) => {
    setEditingId(source.id)
    setForm({
      name: source.name,
      category: source.category,
      system_type: source.system_type,
      description: source.description || '',
      endpoint_url: source.endpoint_url || '',
    })
    setFormError(null)
    setFormOpen(true)
  }

  const handleSave = async () => {
    if (!form.name.trim() || !form.system_type.trim()) {
      setFormError('Name and system type are required.')
      return
    }
    setSaving(true)
    setFormError(null)
    const payload = {
      name: form.name.trim(),
      category: form.category,
      system_type: form.system_type.trim(),
      description: form.description.trim() || undefined,
      endpoint_url: form.endpoint_url.trim() || undefined,
    }
    try {
      if (editingId !== null) {
        await apiClient.patch(`/api/data-manager/sources/${editingId}`, payload)
      } else {
        await apiClient.post('/api/data-manager/sources', payload)
      }
      setFormOpen(false)
      await fetchAll()
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Failed to save data source.')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await apiClient.delete(`/api/data-manager/sources/${deleteTarget.id}`)
      setDeleteTarget(null)
      await fetchAll()
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Failed to delete data source.')
    } finally {
      setDeleting(false)
    }
  }

  const handleCheck = async (id: number) => {
    setCheckingId(id)
    try {
      const updated = await apiClient.post<DataSource>(`/api/data-manager/sources/${id}/check`)
      setSources((prev) => prev.map((s) => (s.id === id ? updated : s)))
      const summaryData = await apiClient.get<DataSourceSummary>('/api/data-manager/summary')
      setSummary(summaryData)
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Health check failed.')
    } finally {
      setCheckingId(null)
    }
  }

  const handleCheckAll = async () => {
    setCheckingAll(true)
    try {
      await apiClient.post('/api/data-manager/sources/check-all')
      await fetchAll()
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Failed to check all sources.')
    } finally {
      setCheckingAll(false)
    }
  }

  return (
    <motion.div className="space-y-6" variants={containerVariants} initial="hidden" animate="visible">
      {/* Header */}
      <motion.div variants={itemVariants} className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-display-3 font-bold tracking-tight text-brand-navy lg:text-display-2">
            Data Manager
          </h1>
          <p className="mt-2 text-lg text-muted-foreground">
            Every system the AI Employee talks to — channels, systems of record, the human
            loop, and the Auto platform — with live, real health status.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={handleCheckAll} disabled={checkingAll || sources.length === 0}>
            {checkingAll ? (
              <Icons.loader className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Icons.refresh className="mr-2 h-4 w-4" strokeWidth={1.5} />
            )}
            Check All
          </Button>
          <Button variant="gradient" onClick={openCreateForm}>
            <Icons.plus className="mr-2 h-4 w-4" strokeWidth={1.5} />
            Add Source
          </Button>
        </div>
      </motion.div>

      {/* Load error banner */}
      {loadError && (
        <motion.div
          variants={itemVariants}
          className="flex items-center justify-between gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
        >
          <div className="flex items-center gap-2">
            <Icons.alertCircle className="h-4 w-4 flex-shrink-0" />
            <span>{loadError}</span>
          </div>
          <Button variant="ghost" size="sm" onClick={() => fetchAll()}>
            <Icons.loader className="mr-1.5 h-3.5 w-3.5" />
            Retry
          </Button>
        </motion.div>
      )}

      {/* KPI cards */}
      <motion.div variants={itemVariants} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <Card className="relative overflow-hidden">
          <CardWatermark opacity={2} scale={0.8} />
          <CardContent className="relative z-10 flex items-center gap-4 py-6">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-brand-navy/10">
              <Icons.network className="h-6 w-6 text-brand-navy" strokeWidth={1.5} />
            </div>
            <div>
              <p className="text-2xl font-bold text-brand-navy">{summary?.total ?? 0}</p>
              <p className="text-sm text-muted-foreground">Connected Systems</p>
            </div>
          </CardContent>
        </Card>
        <Card className="relative overflow-hidden">
          <CardWatermark opacity={2} scale={0.8} />
          <CardContent className="relative z-10 flex items-center gap-4 py-6">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-emerald-100">
              <Icons.checkCircle className="h-6 w-6 text-emerald-600" strokeWidth={1.5} />
            </div>
            <div>
              <p className="text-2xl font-bold text-brand-navy">{summary?.healthy ?? 0}</p>
              <p className="text-sm text-muted-foreground">Healthy</p>
            </div>
          </CardContent>
        </Card>
        <Card className="relative overflow-hidden">
          <CardWatermark opacity={2} scale={0.8} />
          <CardContent className="relative z-10 flex items-center gap-4 py-6">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-amber-100">
              <Icons.alertTriangle className="h-6 w-6 text-amber-600" strokeWidth={1.5} />
            </div>
            <div>
              <p className="text-2xl font-bold text-brand-navy">{summary?.degraded ?? 0}</p>
              <p className="text-sm text-muted-foreground">Degraded</p>
            </div>
          </CardContent>
        </Card>
        <Card className="relative overflow-hidden">
          <CardWatermark opacity={2} scale={0.8} />
          <CardContent className="relative z-10 flex items-center gap-4 py-6">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-red-100">
              <Icons.alertCircle className="h-6 w-6 text-red-600" strokeWidth={1.5} />
            </div>
            <div>
              <p className="text-2xl font-bold text-brand-navy">{summary?.down ?? 0}</p>
              <p className="text-sm text-muted-foreground">Down</p>
            </div>
          </CardContent>
        </Card>
        <Card className="relative overflow-hidden">
          <CardWatermark opacity={2} scale={0.8} />
          <CardContent className="relative z-10 flex items-center gap-4 py-6">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-gray-100">
              <Icons.helpCircle className="h-6 w-6 text-gray-500" strokeWidth={1.5} />
            </div>
            <div>
              <p className="text-2xl font-bold text-brand-navy">{summary?.unconfigured ?? 0}</p>
              <p className="text-sm text-muted-foreground">Unconfigured</p>
            </div>
          </CardContent>
        </Card>
      </motion.div>

      {/* Loading */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <Icons.loader className="h-8 w-8 animate-spin text-brand-cornflower" />
        </div>
      ) : sources.length === 0 && !loadError ? (
        <motion.div variants={itemVariants}>
          <Card className="relative overflow-hidden">
            <CardWatermark opacity={2} scale={1} />
            <CardContent className="relative z-10 flex flex-col items-center justify-center py-12 text-center">
              <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-cornflower/20 to-brand-purple/20">
                <Icons.network className="h-8 w-8 text-brand-cornflower" strokeWidth={1.5} />
              </div>
              <h3 className="font-display text-lg font-semibold text-brand-navy">
                No connected systems registered yet
              </h3>
              <p className="mt-1 max-w-sm text-sm text-muted-foreground">
                Register the channel, system of record, human loop, and Auto platform this AI
                Employee talks to so their live health shows up here.
              </p>
              <Button variant="gradient" className="mt-6" onClick={openCreateForm}>
                <Icons.plus className="mr-2 h-4 w-4" strokeWidth={1.5} />
                Add Source
              </Button>
            </CardContent>
          </Card>
        </motion.div>
      ) : (
        <div className="space-y-6">
          {[...CATEGORY_ORDER, ...extraCategories].map((category) => {
            const items = grouped[category]
            if (!items || items.length === 0) return null
            const meta = CATEGORY_META[category] || {
              label: category,
              icon: Icons.folder,
              blurb: '',
            }
            const CategoryIcon = meta.icon

            return (
              <motion.div key={category} variants={itemVariants}>
                <Card className="relative overflow-hidden">
                  <CardWatermark opacity={2} scale={1} />
                  <CardHeader className="relative z-10">
                    <div className="flex items-center gap-3">
                      <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-navy/10">
                        <CategoryIcon className="h-4.5 w-4.5 text-brand-navy" strokeWidth={1.5} />
                      </div>
                      <div>
                        <CardTitle>{meta.label}</CardTitle>
                        {meta.blurb && <CardDescription>{meta.blurb}</CardDescription>}
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent className="relative z-10 space-y-3">
                    {items.map((source) => (
                      <div
                        key={source.id}
                        className="flex flex-col gap-3 rounded-lg border border-border/60 bg-white/60 p-4 sm:flex-row sm:items-center sm:justify-between"
                      >
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="font-medium text-brand-navy">{source.name}</p>
                            <span className="rounded-md bg-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground">
                              {source.system_type}
                            </span>
                            <StatusBadge status={source.status} />
                          </div>
                          {source.description && (
                            <p className="mt-1 text-sm text-muted-foreground">{source.description}</p>
                          )}
                          <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                            <span className="flex items-center gap-1">
                              <Icons.clock className="h-3 w-3" />
                              checked {timeAgo(source.last_checked_at)}
                            </span>
                            {source.latency_ms != null && (
                              <span>{Math.round(source.latency_ms)}ms</span>
                            )}
                            {source.error_message && (
                              <span className="text-red-600">{source.error_message}</span>
                            )}
                            {source.endpoint_url && (
                              <span className="truncate max-w-[240px]">{source.endpoint_url}</span>
                            )}
                          </div>
                        </div>
                        <div className="flex items-center gap-1.5">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleCheck(source.id)}
                            disabled={checkingId === source.id}
                          >
                            {checkingId === source.id ? (
                              <Icons.loader className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <Icons.refresh className="h-3.5 w-3.5" strokeWidth={1.5} />
                            )}
                            <span className="ml-1.5 hidden sm:inline">Check</span>
                          </Button>
                          <Button variant="ghost" size="sm" onClick={() => openEditForm(source)}>
                            <Icons.edit className="h-3.5 w-3.5" strokeWidth={1.5} />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="text-red-600 hover:bg-red-50 hover:text-red-700"
                            onClick={() => setDeleteTarget(source)}
                          >
                            <Icons.trash className="h-3.5 w-3.5" strokeWidth={1.5} />
                          </Button>
                        </div>
                      </div>
                    ))}
                  </CardContent>
                </Card>
              </motion.div>
            )
          })}
        </div>
      )}

      {/* Add/Edit source dialog */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editingId !== null ? 'Edit Data Source' : 'Add Data Source'}</DialogTitle>
            <DialogDescription>
              Register a connected system. Credentials come from environment variables, never
              from this form — only non-secret metadata is stored here.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            {formError && (
              <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                <Icons.alertCircle className="h-4 w-4 flex-shrink-0" />
                <span>{formError}</span>
              </div>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="ds-name">Name</Label>
              <Input
                id="ds-name"
                placeholder="e.g. Orders & Inventory Store"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="ds-category">Category</Label>
                <Select
                  value={form.category}
                  onValueChange={(v) => setForm((f) => ({ ...f, category: v }))}
                >
                  <SelectTrigger id="ds-category">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {CATEGORY_ORDER.map((c) => (
                      <SelectItem key={c} value={c}>
                        {CATEGORY_META[c].label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="ds-type">System Type</Label>
                <Input
                  id="ds-type"
                  list="ds-type-suggestions"
                  placeholder="e.g. slack, supabase, auto"
                  value={form.system_type}
                  onChange={(e) => setForm((f) => ({ ...f, system_type: e.target.value }))}
                />
                <datalist id="ds-type-suggestions">
                  {SYSTEM_TYPE_SUGGESTIONS.map((t) => (
                    <option key={t} value={t} />
                  ))}
                </datalist>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="ds-description">Description</Label>
              <Input
                id="ds-description"
                placeholder="What this system is for"
                value={form.description}
                onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="ds-endpoint">Endpoint URL</Label>
              <Input
                id="ds-endpoint"
                placeholder="https://... (used for the generic reachability check)"
                value={form.endpoint_url}
                onChange={(e) => setForm((f) => ({ ...f, endpoint_url: e.target.value }))}
              />
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)} disabled={saving}>
              Cancel
            </Button>
            <Button variant="gradient" onClick={handleSave} disabled={saving}>
              {saving ? (
                <Icons.loader className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Icons.check className="mr-2 h-4 w-4" strokeWidth={1.5} />
              )}
              {editingId !== null ? 'Save Changes' : 'Add Source'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirmation */}
      <AlertDialog open={deleteTarget !== null} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove data source?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes <span className="font-medium text-foreground">{deleteTarget?.name}</span>{' '}
              from the registry. This can&apos;t be undone, though you can always re-add it.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
              disabled={deleting}
              className="bg-red-600 hover:bg-red-700"
            >
              {deleting ? <Icons.loader className="mr-2 h-4 w-4 animate-spin" /> : null}
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </motion.div>
  )
}

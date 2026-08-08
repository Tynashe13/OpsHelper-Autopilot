'use client'

import { useState, useRef, useEffect } from 'react'
import { motion } from 'framer-motion'
import { cn } from '@/lib/utils'
import { apiClient } from '@/lib/api-client'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { CardWatermark } from '@/components/ui/card-watermark'
import { Icons } from '@/components/ui/icons'

// Matches app/schemas/ai_manager.py's AIManagerMessageResponse.
interface ActivityRun {
  id?: string
  stepId?: string
  stepName?: string
  status?: string
  outputs?: Record<string, unknown>
}

interface AIManagerMessageResponse {
  run_id: string | null
  status: string
  outputs: Record<string, unknown>
  activity_runs: ActivityRun[]
  created_at: string
}

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  run?: AIManagerMessageResponse
  error?: string
}

const containerVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.08 } },
}

const itemVariants = {
  hidden: { opacity: 0, y: 20 },
  visible: { opacity: 1, y: 0 },
}

function statusBadgeClass(status?: string) {
  switch ((status || '').toLowerCase()) {
    case 'completed':
    case 'success':
      return 'bg-emerald-100 text-emerald-700'
    case 'failed':
    case 'error':
      return 'bg-red-100 text-red-700'
    case 'running':
    case 'in_progress':
      return 'bg-blue-100 text-blue-700'
    default:
      return 'bg-muted text-muted-foreground'
  }
}

function OrchestratorTrace({ run }: { run: AIManagerMessageResponse }) {
  return (
    <div className="mt-3 rounded-lg border border-border/50 bg-white/60 p-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
          <Icons.bot className="h-3.5 w-3.5" />
          Orchestrator run{run.run_id ? ` · ${run.run_id}` : ''}
        </div>
        <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium', statusBadgeClass(run.status))}>
          {run.status}
        </span>
      </div>

      {run.activity_runs.length > 0 ? (
        <ol className="mt-2 space-y-1.5">
          {run.activity_runs.map((step, idx) => (
            <li key={step.id || step.stepId || idx} className="flex items-center gap-2 text-xs">
              <span className="flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full bg-brand-navy/10 text-[10px] font-medium text-brand-navy">
                {idx + 1}
              </span>
              <span className="flex-1 text-foreground">{step.stepName || step.stepId || 'Operator step'}</span>
              <span className={cn('rounded-full px-1.5 py-0.5 text-[10px] font-medium', statusBadgeClass(step.status))}>
                {step.status || 'unknown'}
              </span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="mt-2 text-xs text-muted-foreground">No per-step trace was returned for this run.</p>
      )}
    </div>
  )
}

export default function AIManagerPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [isSending, setIsSending] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSend = async () => {
    const text = input.trim()
    if (!text || isSending) return

    const userMessage: ChatMessage = { id: crypto.randomUUID(), role: 'user', content: text }
    setMessages((prev) => [...prev, userMessage])
    setInput('')
    setIsSending(true)

    try {
      const data = await apiClient.post<AIManagerMessageResponse>('/api/ai-manager/messages', {
        message: text,
      })
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          content:
            typeof data.outputs?.summary === 'string'
              ? (data.outputs.summary as string)
              : `Orchestrator run finished with status: ${data.status}.`,
          run: data,
        },
      ])
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: 'The Orchestrator could not be reached.',
          error: err instanceof Error ? err.message : 'Unknown error contacting the Orchestrator.',
        },
      ])
    } finally {
      setIsSending(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <motion.div className="flex h-[calc(100vh-8rem)] flex-col space-y-6" variants={containerVariants} initial="hidden" animate="visible">
      <motion.div variants={itemVariants}>
        <h1 className="text-display-3 font-bold tracking-tight text-brand-navy lg:text-display-2">
          AI Manager
        </h1>
        <p className="mt-2 text-lg text-muted-foreground">
          Ask the operation a question and watch the live Orchestrator delegate to its Operators.
        </p>
      </motion.div>

      <motion.div variants={itemVariants} className="flex-1 overflow-hidden">
        <Card className="relative flex h-full flex-col overflow-hidden">
          <CardWatermark opacity={2} scale={1} />
          <CardContent className="relative z-10 flex h-full flex-col p-0">
            <div className="flex-1 space-y-4 overflow-y-auto p-6">
              {messages.length === 0 ? (
                <div className="flex h-full flex-col items-center justify-center text-center">
                  <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-cornflower/20 to-brand-purple/20">
                    <Icons.messageSquare className="h-8 w-8 text-brand-cornflower" strokeWidth={1.5} />
                  </div>
                  <h3 className="font-display text-lg font-semibold text-brand-navy">
                    Talk to the Orchestrator
                  </h3>
                  <p className="mt-1 max-w-sm text-sm text-muted-foreground">
                    e.g. &ldquo;What&apos;s the recovery plan for the latest critical disruption?&rdquo;
                  </p>
                </div>
              ) : (
                messages.map((msg) => (
                  <div
                    key={msg.id}
                    className={cn('flex', msg.role === 'user' ? 'justify-end' : 'justify-start')}
                  >
                    <div className={cn('max-w-[80%] rounded-2xl px-4 py-3 text-sm', msg.role === 'user' ? 'bg-brand-navy text-white' : 'bg-muted/60 text-foreground')}>
                      <p>{msg.content}</p>
                      {msg.error && (
                        <p className="mt-1 flex items-center gap-1.5 text-xs text-red-600">
                          <Icons.alertCircle className="h-3.5 w-3.5" />
                          {msg.error}
                        </p>
                      )}
                      {msg.run && <OrchestratorTrace run={msg.run} />}
                    </div>
                  </div>
                ))
              )}
              <div ref={scrollRef} />
            </div>

            <div className="border-t border-border/50 p-4">
              <div className="flex items-end gap-2">
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Ask the operation a question..."
                  rows={1}
                  className="flex-1 resize-none rounded-lg border border-border/50 bg-white/70 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-brand-cornflower/50"
                  disabled={isSending}
                />
                <Button variant="gradient" onClick={handleSend} disabled={isSending || !input.trim()}>
                  {isSending ? (
                    <Icons.loader className="h-4 w-4 animate-spin" />
                  ) : (
                    <Icons.send className="h-4 w-4" strokeWidth={1.5} />
                  )}
                </Button>
              </div>
              <p className="mt-2 text-xs text-muted-foreground">
                Sends to the live Ops Helper orchestrator on Supervity Auto and returns its full run trace.
              </p>
            </div>
          </CardContent>
        </Card>
      </motion.div>
    </motion.div>
  )
}

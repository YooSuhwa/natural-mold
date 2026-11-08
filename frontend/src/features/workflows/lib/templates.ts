import type { TemplateSegment, WorkflowDefinition } from '@/lib/types/workflow'

/** Workflow templates offered when creating a workflow (workflow-plan 3.7 "시작"). */
export const WORKFLOW_TEMPLATES = ['blank', 'inquiry'] as const
export type WorkflowTemplate = (typeof WORKFLOW_TEMPLATES)[number]

type Translate = (key: string) => string

const text = (value: string): TemplateSegment => ({ text: value })
const ref = (node: string, path: string): TemplateSegment => ({ ref: { node, path } })

/**
 * Q3 업무 "고객 문의 답변 초안": classify (category, urgency) → urgent? → manager approval →
 * answer draft → transform → output; a rejection ends in failure. Uses all seven node kinds.
 * ``t`` is the ``workflows.templates.inquiry`` translator: labels and texts are user copy.
 */
export function inquiryTemplate(
  agents: { classifier: string; writer: string },
  t: Translate,
): WorkflowDefinition {
  return {
    schema_version: 'moldy.workflow.v1',
    inputs: [
      { name: 'inquiry', type: 'string', required: true, label: t('inputLabel'), max_length: 4000 },
    ],
    nodes: [
      { id: 'start', type: 'start' },
      {
        id: 'classify',
        type: 'agent',
        label: t('classify'),
        agent_id: agents.classifier,
        prompt: [text(t('classifyPrompt')), ref('input', '/inquiry')],
        output_schema: {
          type: 'object',
          properties: {
            category: {
              type: 'string',
              enum: ['refund', 'bug', 'account', 'other'],
              description: t('categoryHint'),
            },
            urgent: { type: 'boolean', description: t('urgentHint') },
            summary: { type: 'string', description: t('summaryHint') },
          },
          required: ['category', 'urgent', 'summary'],
        },
      },
      {
        id: 'is_urgent',
        type: 'decision_bool',
        label: t('isUrgent'),
        condition: { all: [{ source: 'classify', path: '/urgent', op: 'eq', value: true }] },
      },
      {
        id: 'review',
        type: 'approval',
        label: t('review'),
        title: [text(t('reviewTitle')), ref('classify', '/summary')],
        body: [ref('input', '/inquiry')],
        timeout_minutes: 1440,
      },
      {
        id: 'draft',
        type: 'agent',
        label: t('draft'),
        agent_id: agents.writer,
        prompt: [
          text(t('draftPrompt')),
          ref('input', '/inquiry'),
          text(t('draftCategory')),
          ref('classify', '/category'),
        ],
        output_schema: {
          type: 'object',
          properties: { answer: { type: 'string', description: t('answerHint') } },
          required: ['answer'],
        },
      },
      {
        id: 'format',
        type: 'transform',
        label: t('format'),
        fields: {
          subject: [text(t('subjectPrefix')), ref('classify', '/summary')],
          body: [ref('draft', '/answer')],
        },
      },
      {
        id: 'done',
        type: 'output',
        label: t('done'),
        fields: {
          subject: [ref('format', '/fields/subject')],
          body: [ref('format', '/fields/body')],
          category: [ref('classify', '/category')],
        },
      },
      { id: 'rejected', type: 'fail', label: t('rejected'), reason: t('rejectedReason') },
    ],
    edges: [
      { from: 'start', to: 'classify' },
      { from: 'classify', to: 'is_urgent' },
      { from: 'is_urgent', to: 'review', when: 'true' },
      { from: 'is_urgent', to: 'draft', when: 'false' },
      { from: 'review', to: 'draft', when: 'approved' },
      { from: 'review', to: 'rejected', when: 'rejected' },
      { from: 'draft', to: 'format' },
      { from: 'format', to: 'done' },
    ],
  }
}

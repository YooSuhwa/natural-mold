'use client'

import { useTranslations } from 'next-intl'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { Skeleton } from '@/components/ui/skeleton'
import {
  CompanyAdminGate,
  type CompanyScope,
} from '@/features/company-settings/components/company-admin-gate'
import { useGuardrailPolicy } from '@/lib/hooks/use-guardrails'
import { GuardrailPolicyForm } from './guardrail-policy-form'
import { GuardrailRegistrationCard } from './guardrail-registration-card'
import { GuardrailToolPoliciesCard } from './guardrail-tool-policies-card'
import { GuardrailTemplatesCard } from './guardrail-templates-card'
import { GuardrailToolRiskForm } from './guardrail-tool-risk-form'

/** Company guardrails: members read the policy, company admins edit it and the templates. */
export function CompanyGuardrailsContent() {
  const t = useTranslations('guardrails')
  return (
    <CompanyAdminGate title={t('title')} description={t('description')}>
      {(scope) => <GuardrailsBody scope={scope} />}
    </CompanyAdminGate>
  )
}

function GuardrailsBody({ scope }: { scope: CompanyScope }) {
  const policy = useGuardrailPolicy(scope.tenantId)
  if (policy.isLoading) return <Skeleton className="h-32 w-full" />
  if (!policy.data) {
    return <ApiErrorState error={policy.error} onRetry={() => void policy.refetch()} />
  }
  return (
    <div className="space-y-4">
      <GuardrailPolicyForm
        key={policy.data.updated_at}
        tenantId={scope.tenantId}
        policy={policy.data}
        readOnly={!scope.isAdmin}
      />
      <GuardrailToolRiskForm
        key={`tool-risk-${policy.data.updated_at}`}
        tenantId={scope.tenantId}
        policy={policy.data}
        readOnly={!scope.isAdmin}
      />
      <GuardrailToolPoliciesCard tenantId={scope.tenantId} isAdmin={scope.isAdmin} />
      <GuardrailRegistrationCard tenantId={scope.tenantId} isAdmin={scope.isAdmin} />
      {scope.isAdmin ? (
        <GuardrailTemplatesCard tenantId={scope.tenantId} choices={policy.data.templates} />
      ) : null}
    </div>
  )
}

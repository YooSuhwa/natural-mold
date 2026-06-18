'use client'

import { Loader2 } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { CredentialCreateModal } from '@/components/credential/credential-create-modal'
import { DialogShell } from '@/components/shared/dialog-shell'
import { DomainIconTile } from '@/components/shared/icon'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

import { McpWizardCredentialsSection } from './mcp-wizard-credentials-section'
import { useMcpWizardController } from './mcp-wizard-controller'
import { McpWizardProbeBadge, McpWizardProbeSection } from './mcp-wizard-probe-section'
import { McpWizardTransportSection } from './mcp-wizard-transport-section'

type McpServerWizardProps = {
  readonly open: boolean
  readonly onOpenChange: (open: boolean) => void
}

export function McpServerWizard({ open, onOpenChange }: McpServerWizardProps) {
  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="lg" height="tall">
      {open ? <McpWizardBody onClose={() => onOpenChange(false)} /> : null}
    </DialogShell>
  )
}

function McpWizardBody({ onClose }: { readonly onClose: () => void }) {
  const t = useTranslations('mcp.wizard')
  const wizard = useMcpWizardController({ onClose })

  return (
    <>
      <DialogShell.Header
        icon={<DomainIconTile iconId="mcp" className="size-9" iconClassName="size-5" />}
        title={t('title')}
        description={t('description')}
        actions={<McpWizardProbeBadge state={wizard.probeState} />}
      />
      <DialogShell.Body>
        <Tabs value={wizard.tab} onValueChange={wizard.handleTabChange}>
          <TabsList variant="line">
            <TabsTrigger value="basics">{t('tabs.basics')}</TabsTrigger>
            <TabsTrigger value="auth">{t('tabs.auth')}</TabsTrigger>
            <TabsTrigger value="tools">{t('tabs.tools')}</TabsTrigger>
          </TabsList>

          <TabsContent value="basics" className="pt-4">
            <McpWizardTransportSection
              registry={wizard.registry}
              state={wizard.form}
              onChange={wizard.updateForm}
              onPickRegistry={wizard.handlePickRegistryEntry}
              onClearRegistry={wizard.clearRegistry}
              onTransportChange={wizard.handleTransportChange}
              onAddArg={wizard.handleAddArg}
            />
            <div className="mt-6 flex justify-end">
              <Button onClick={() => wizard.setTab('auth')} disabled={!wizard.basicsValid}>
                {t('actions.continueAuth')}
              </Button>
            </div>
          </TabsContent>

          <TabsContent value="auth" className="pt-4">
            <McpWizardCredentialsSection
              credentialId={wizard.form.credentialId}
              onCredentialChange={(credentialId) => wizard.updateForm({ credentialId })}
              credentialDefinitionFilter={wizard.form.credentialDefinitionFilter}
              usesMcpOAuth={wizard.usesMcpOAuth}
              onCreateOAuthCredential={() => wizard.setCredentialCreateOpen(true)}
              onConnectOAuth={() => void wizard.handleOAuthConnect()}
              oauthStarting={wizard.oauthStarting}
              oauthWaiting={wizard.oauthWaiting}
              oauthConnected={wizard.oauthConnected}
              probeState={wizard.probeState}
              onTest={wizard.runProbe}
              testing={wizard.testing}
            />
            <div className="mt-6 flex justify-end gap-2">
              <Button variant="outline" onClick={() => wizard.setTab('basics')}>
                {t('actions.back')}
              </Button>
              <Button onClick={() => wizard.setTab('tools')}>{t('actions.continueTools')}</Button>
            </div>
          </TabsContent>

          <TabsContent value="tools" className="pt-4">
            <McpWizardProbeSection
              probeState={wizard.probeState}
              tools={wizard.discoveredTools}
              onRetry={() => void wizard.runProbe()}
            />
          </TabsContent>
        </Tabs>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose} disabled={wizard.saving}>
          {t('actions.cancel')}
        </Button>
        <Button onClick={wizard.handleSave} disabled={wizard.saving || !wizard.basicsValid}>
          {wizard.saving ? <Loader2 className="size-4 animate-spin" /> : null}
          {t('actions.save')}
        </Button>
      </DialogShell.Footer>
      {wizard.credentialCreateOpen ? (
        <CredentialCreateModal
          open={wizard.credentialCreateOpen}
          onOpenChange={wizard.setCredentialCreateOpen}
          presetDefinitionKey="mcp_oauth2"
          initialName={wizard.oauthCredentialInitialName}
          initialData={wizard.oauthCredentialInitialData}
          onCreated={wizard.handleCreatedCredential}
        />
      ) : null}
    </>
  )
}

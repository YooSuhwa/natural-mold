import { redirect } from 'next/navigation'

export default function CredentialsRedirectPage() {
  redirect('/settings/credentials')
}

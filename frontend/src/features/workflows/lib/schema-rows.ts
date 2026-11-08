/**
 * The output schema as a table (workflow-plan 3.7): one row per top-level field. The table
 * covers what most steps need (text, numbers, yes/no, a choice, a list of text); a schema
 * the table cannot show (nested objects) is kept as is and edited as JSON later.
 */
export type FieldType = 'string' | 'number' | 'integer' | 'boolean' | 'enum' | 'string_list'

export interface FieldRow {
  name: string
  type: FieldType
  required: boolean
  description: string
  options: string[] // enum values
}

type Property = {
  type?: string
  enum?: string[]
  description?: string
  items?: { type?: string }
}

export function schemaToRows(schema: Record<string, unknown>): FieldRow[] | null {
  const properties = (schema.properties ?? {}) as Record<string, Property>
  const required = new Set((schema.required ?? []) as string[])
  const rows: FieldRow[] = []
  for (const [name, property] of Object.entries(properties)) {
    let type: FieldType
    if (property.type === 'string' && property.enum) type = 'enum'
    else if (property.type === 'array' && property.items?.type === 'string') type = 'string_list'
    else if (['string', 'number', 'integer', 'boolean'].includes(property.type ?? '')) {
      type = property.type as FieldType
    } else return null // nested objects or other arrays: not a table
    rows.push({
      name,
      type,
      required: required.has(name),
      description: property.description ?? '',
      options: property.enum ?? [],
    })
  }
  return rows
}

export function rowsToSchema(rows: readonly FieldRow[]): Record<string, unknown> {
  const properties: Record<string, Property> = {}
  for (const row of rows) {
    const property: Property =
      row.type === 'enum'
        ? { type: 'string', enum: row.options.filter(Boolean) }
        : row.type === 'string_list'
          ? { type: 'array', items: { type: 'string' } }
          : { type: row.type }
    if (row.description) property.description = row.description
    properties[row.name] = property
  }
  return {
    type: 'object',
    properties,
    required: rows.filter((row) => row.required).map((row) => row.name),
  }
}

/** "facts[], sources[]": the one-line output contract a node card shows. */
export function contractLine(schema: Record<string, unknown>): string {
  const properties = (schema.properties ?? {}) as Record<string, Property>
  return Object.entries(properties)
    .map(([name, property]) => (property.type === 'array' ? `${name}[]` : name))
    .join(', ')
}

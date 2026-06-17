export type JsonPrimitive = string | number | boolean | null
export type JsonObject = { readonly [key: string]: JsonValue }
export type JsonValue = JsonPrimitive | readonly JsonValue[] | JsonObject

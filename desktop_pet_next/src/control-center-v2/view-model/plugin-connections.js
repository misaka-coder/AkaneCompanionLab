import { asObject, text } from "./shared.js";

export function normalizePluginContributions(value) {
  const contributions = asObject(value);
  const normalized = {};
  for (const key of ["capabilities", "commands", "event_handlers", "hooks", "background_services", "prompt_blocks", "skills"]) {
    normalized[key] = (Array.isArray(contributions[key]) ? contributions[key] : []).map(text).filter(Boolean);
  }
  normalized.connections = normalizePluginConnectionDeclarations(contributions.connections);
  return normalized;
}

function normalizePluginConnectionDeclarations(value) {
  return (Array.isArray(value) ? value : []).map((item) => {
    const entry = asObject(item);
    const schema = normalizePluginConnectionSchema(entry.schema);
    const properties = asObject(schema.properties);
    const privateFields = (Array.isArray(entry.private_fields) ? entry.private_fields : [])
      .map(text)
      .filter((field) => Object.hasOwn(properties, field));
    return {
      name: text(entry.name),
      version: Math.max(1, Number(entry.version) || 1),
      description: text(entry.description),
      schema,
      privateFields
    };
  }).filter((item) => item.name && item.schema.type === "object");
}

function normalizePluginConnectionSchema(value) {
  const source = asObject(value);
  const properties = asObject(source.properties);
  const normalizedProperties = {};
  for (const [key, raw] of Object.entries(properties)) {
    const field = asObject(raw);
    const type = ["string", "number", "integer", "boolean"].includes(text(field.type)) ? text(field.type) : "string";
    const next = { type };
    for (const property of ["title", "description", "default", "minimum", "maximum", "pattern", "format", "step"]) {
      if (field[property] !== undefined && field[property] !== null) next[property] = field[property];
    }
    if (Array.isArray(field.enum)) next.enum = field.enum.slice(0, 40);
    normalizedProperties[key] = next;
  }
  return {
    type: "object",
    properties: normalizedProperties,
    required: (Array.isArray(source.required) ? source.required : []).map(text).filter((key) => Object.hasOwn(normalizedProperties, key)),
    additionalProperties: source.additionalProperties === true
  };
}

export function normalizePluginConnectionConfigs(value, declarations) {
  const configs = Array.isArray(value) ? value : [];
  return (Array.isArray(declarations) ? declarations : []).map((declaration) => {
    const entry = configs.find((item) => text(asObject(item).connection?.name) === declaration.name) || {};
    const values = asObject(entry.values);
    const privateFields = Array.from(new Set([
      ...declaration.privateFields,
      ...(Array.isArray(entry.private_fields) ? entry.private_fields.map(text) : [])
    ])).filter((field) => Object.hasOwn(declaration.schema.properties, field));
    return {
      ...declaration,
      status: text(entry.status) || "not_configured",
      reason: text(entry.reason),
      revision: Math.max(0, Number(entry.revision) || 0),
      enabled: entry.enabled === true,
      values: Object.fromEntries(Object.entries(values).filter(([key]) => !privateFields.includes(key))),
      configuredPrivateFields: (Array.isArray(entry.configured_private_fields) ? entry.configured_private_fields : [])
        .map(text)
        .filter((field) => privateFields.includes(field)),
      privateFields
    };
  });
}

import Ajv2020 from "ajv/dist/2020";
import schema from "./scene.schema.json";

const ajv = new Ajv2020({ strict: false, allErrors: true });
ajv.addSchema(schema, "scene");
export function validate<T>(name: string, value: unknown): T {
  const check = ajv.getSchema(`scene#/$defs/${name}`);
  if (!check || !check(value)) throw Error(`宿主返回的${name}格式不匹配，请更新宿主。`);
  return value as T;
}

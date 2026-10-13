// Legacy event-consumer tests isolate function bodies with transport/UI doubles.
// Resolve declarations by AST across the runtime modules, never by file offsets.
// The real controller graph is exercised separately by pet-runtime-smoke.mjs.
import fs from "node:fs";
import ts from "typescript";
import nodeVm from "node:vm";
import { SpeechStageReplay } from "../src/speech-delivery.js";
import { SPEECH_UNITS_PROTOCOL, VoiceStageReplay, utteranceVoiceSegments, utteranceDeliveryEvents, normalizeUnitEmotion } from "../src/speech-units.js";

const directory = new URL("../src/pet/", import.meta.url);
const declarations = new Map();
const modules = [];
const canonical = source => source.replace(/\b[A-Za-z]+Port\./g, "").replace(/^  /gm, "");
for (const name of fs.readdirSync(directory, { recursive: true }).filter(name => name.endsWith(".js")).map(name => name.replaceAll("\\", "/"))) {
  const raw = fs.readFileSync(new URL(name, directory), "utf8");
  modules.push(canonical(raw));
  const source = ts.createSourceFile(name, raw, ts.ScriptTarget.Latest, true);
  function collect(statements) {
    for (const node of statements) {
      if (ts.isFunctionDeclaration(node)) {
        if (/^create.*Controller$|^createPetApplication$/.test(node.name.text) && node.body) collect(node.body.statements);
        else declarations.set(node.name.text, canonical(node.getText(source)));
      } else if (ts.isVariableStatement(node)) {
        for (const item of node.declarationList.declarations) {
          if (ts.isIdentifier(item.name)) declarations.set(item.name.text, canonical(node.getText(source)));
        }
      }
    }
  }
  collect(source.statements);
}

export function petRuntimeSource() { return modules.join("\n\n"); }
export function petFunctionSource(name) {
  const declaration = declarations.get(name);
  if (!declaration) throw new Error(`Pet runtime declaration missing: ${name}`);
  return declaration;
}
export function petFunctions(names) { return names.map(petFunctionSource).join("\n\n"); }

export function petListenerSource(element, event) {
  const code = petFunctionSource("bindUi");
  const source = ts.createSourceFile("ui.js", code, ts.ScriptTarget.Latest, true);
  let result;
  function walk(node) {
    if (ts.isCallExpression(node) && node.expression.getText(source) === "lifecycle.on" &&
        node.arguments[0]?.getText(source) === `els.${element}` && node.arguments[1]?.text === event) {
      result = node.getText(source) + ";";
    }
    ts.forEachChild(node, walk);
  }
  walk(source);
  if (!result) throw new Error(`Pet listener missing: ${element}/${event}`);
  return result;
}

function calledFunctions(code) {
  const names = new Set();
  const source = ts.createSourceFile("test.js", code, ts.ScriptTarget.Latest, true);
  function walk(node) {
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression)) names.add(node.expression.text);
    ts.forEachChild(node, walk);
  }
  walk(source);
  return names;
}

export default {
  createContext(context) {
    const timers = { setTimeout, clearTimeout, setInterval, clearInterval };
    context.lifecycle ??= { ...timers, ...context.window, disposed: false,
      on: (target, event, handler, options) => target.addEventListener(event, handler, options) };
    context.runtimeScope ??= { capture: () => ({}), isCurrent: () => true };
    context.activeTurnScope ??= null;
    context.voiceSending ??= false;
    context.commandRevisions ??= new Map();
    context.SpeechStageReplay ??= SpeechStageReplay;
    context.VoiceStageReplay ??= VoiceStageReplay;
    context.SPEECH_UNITS_PROTOCOL ??= SPEECH_UNITS_PROTOCOL;
    context.utteranceVoiceSegments ??= utteranceVoiceSegments;
    context.utteranceDeliveryEvents ??= utteranceDeliveryEvents;
    context.normalizeUnitEmotion ??= normalizeUnitEmotion;
    context.streamedReplyUnitKeys ??= [];
    context.streamedReplySeenUnitKeys ??= new Set();
    context.streamedReplyUnitEmotions ??= new Map();
    context.getActiveCharacterProfile ??= () => ({ voice: { provider: "gpt_sovits" } });
    return nodeVm.createContext(context);
  },
  runInContext(code, context) {
    const defined = new Set([...code.matchAll(/function\s+([A-Za-z]\w*)\(/g)].map(match => match[1]));
    const extras = [];
    const queue = [...calledFunctions(code)];
    while (queue.length) {
      const name = queue.shift();
      if (defined.has(name) || name in context || !declarations.has(name)) continue;
      const declaration = declarations.get(name);
      if (!/^(?:async )?function/.test(declaration)) continue;
      defined.add(name);
      extras.push(declaration);
      queue.push(...calledFunctions(declaration));
    }
    return nodeVm.runInContext(extras.join("\n\n") + "\n" + code, context);
  }
};

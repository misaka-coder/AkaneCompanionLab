//! Immutable aliases for observations, not grants or a second desktop session.
use std::collections::{HashMap, VecDeque};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use super::protocol::string;

#[derive(Default)]
pub struct Contexts {
    entries: HashMap<String, Value>,
    order: VecDeque<String>,
}
impl Contexts {
    pub fn publish(&mut self, scope: &str, epoch: &str, result: &mut Value) {
        // Only an actual returned observation gets an alias. Errors cannot
        // manufacture evidence by inheriting whatever happens to be current.
        if ["control_session_id", "window_id", "observation_id"].iter().any(|k| string(result,k).is_empty()) { return; }
        let shot = result["screenshots"].as_array().and_then(|a|a.first()).map(|v|string(v,"screenshot_id")).unwrap_or("");
        let binding = json!({"scope":scope,"device_epoch":epoch,"contract":super::protocol::SCHEMA_HASH,
            "window_id":result["window_id"],"control_session_id":result["control_session_id"],
            "observation_id":result["observation_id"],"screenshot_id":shot});
        // 128-bit digest of the exact tuple, including device generation and
        // contract. Never bind an existing alias to a different tuple.
        let digest = Sha256::digest(binding.to_string().as_bytes());
        let key = format!("ctx_{}",URL_SAFE_NO_PAD.encode(&digest[..16]));
        if let Some(old) = self.entries.get(&key) { if old != &binding { return; } }
        else {
            while self.order.len() >= 512 {
                if let Some(old) = self.order.pop_front() { self.entries.remove(&old); }
            }
            self.entries.insert(key.clone(),binding); self.order.push_back(key.clone());
        }
        result["context_ref"] = json!(key);
    }

    pub fn expand(&self, args:&Value, scope:&str, epoch:&str) -> Result<Value,&'static str> {
        if args.get("context_ref").is_none() {return Ok(args.clone());}
        let binding = self.entries.get(string(args,"context_ref")).ok_or("context_reference_expired")?;
        if string(binding,"scope")!=scope {return Err("context_scope_mismatch");}
        if string(binding,"device_epoch")!=epoch || string(binding,"contract")!=super::protocol::SCHEMA_HASH {
            return Err("context_reference_expired");
        }
        let object = args.as_object().ok_or("invalid_arguments")?;
        let expanded = super::protocol::context_arguments(object,binding).ok_or("context_evidence_unavailable")?;
        Ok(Value::Object(expanded))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn observation(id:&str)->Value {json!({"control_session_id":"c","window_id":"w","observation_id":id,
        "screenshots":[{"screenshot_id":format!("shot-{id}")}],"action_state":"executed"})}
    #[test]
    fn aliases_are_exact_scope_generation_and_observation_bindings() {
        let mut store=Contexts::default(); let mut first=observation("o1");store.publish("scope","epoch",&mut first);
        let click=json!({"action":"click","context_ref":first["context_ref"],"x":1,"y":2});
        let canonical=store.expand(&click,"scope","epoch").unwrap();
        assert_eq!(canonical["observation_id"],"o1");assert_eq!(canonical["screenshot_id"],"shot-o1");
        assert_eq!(canonical["control_session_id"],"c");assert_eq!(canonical["device_epoch"],"epoch");
        assert_eq!(store.expand(&click,"other","epoch").unwrap_err(),"context_scope_mismatch");
        assert_eq!(store.expand(&click,"scope","reconnected").unwrap_err(),"context_reference_expired");
        let mut newer=observation("o2");newer["window_id"]=json!("different-window");store.publish("scope","epoch",&mut newer);
        assert_ne!(first["context_ref"],newer["context_ref"]);
        assert_eq!(store.expand(&click,"scope","epoch").unwrap()["observation_id"],"o1");
        assert!(Contexts::default().expand(&click,"scope","epoch").is_err());
        for n in 0..512 {store.publish("scope","epoch",&mut observation(&format!("new-{n}")));}
        assert_eq!(store.expand(&click,"scope","epoch").unwrap_err(),"context_reference_expired");
    }
    #[test]
    fn text_observation_cannot_supply_a_screenshot_and_missing_fields_stay_missing() {
        let mut store=Contexts::default();let mut out=observation("o");out["screenshots"]=json!([]);
        store.publish("scope","epoch",&mut out);
        let args=json!({"action":"click","context_ref":out["context_ref"],"x":1,"y":2});
        assert_eq!(store.expand(&args,"scope","epoch").unwrap_err(),"context_evidence_unavailable");
        let observe=json!({"action":"observe","context_ref":out["context_ref"]});
        let canonical=store.expand(&observe,"scope","epoch").unwrap();
        assert!(canonical.get("observation_id").is_none());assert!(canonical.get("screenshot_id").is_none());
        let mut error=json!({"ok":false,"reason":"capture_failed"});store.publish("scope","epoch",&mut error);
        assert!(error.get("context_ref").is_none());
    }
}

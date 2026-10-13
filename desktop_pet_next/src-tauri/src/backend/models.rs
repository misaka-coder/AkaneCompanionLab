use serde::{Deserialize, Serialize};

use crate::backend::policy::default_backend_admin_method;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct BackendInstanceVerification {
    pub(crate) ok: bool,
    pub(crate) status: String,
    pub(crate) reason: String,
    pub(crate) instance_id: String,
    pub(crate) actual_instance_id: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct BackendAdminRequest {
    pub(crate) url: String,
    #[serde(default = "default_backend_admin_method")]
    pub(crate) method: String,
    #[serde(default)]
    pub(crate) body: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct BackendAdminResponse {
    pub(crate) ok: bool,
    pub(crate) status: String,
    pub(crate) reason: String,
    pub(crate) http_status: u16,
    pub(crate) content_type: String,
    pub(crate) body: String,
}

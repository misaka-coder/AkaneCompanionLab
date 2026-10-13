use std::net::IpAddr;

pub(crate) fn normalize_public_external_url(raw_url: &str) -> Result<String, String> {
    let url = raw_url.trim();
    if url.is_empty() || url.len() > 1600 {
        return Err("网址为空或过长。".to_string());
    }
    if url.chars().any(|ch| ch.is_control() || ch.is_whitespace()) {
        return Err("网址包含不安全字符。".to_string());
    }

    let parsed = reqwest::Url::parse(url).map_err(|_| "网址主机不安全。".to_string())?;
    if !matches!(parsed.scheme(), "http" | "https") {
        return Err("只支持 http/https 网址。".to_string());
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err("网址主机不安全。".to_string());
    }
    if is_private_or_local_host(parsed.host_str().unwrap_or("")) {
        return Err("不能打开 localhost 或内网地址。".to_string());
    }
    Ok(url.to_string())
}

pub(crate) fn is_private_or_local_host(host: &str) -> bool {
    let lowered = host
        .trim()
        .trim_matches(['[', ']'])
        .trim_end_matches('.')
        .to_ascii_lowercase();
    if lowered.is_empty()
        || lowered == "localhost"
        || lowered.ends_with(".localhost")
        || lowered.ends_with(".local")
    {
        return true;
    }
    if let Ok(ip) = lowered.parse::<IpAddr>() {
        return ip.is_loopback()
            || ip.is_unspecified()
            || ip.is_multicast()
            || match ip {
                IpAddr::V4(value) => {
                    value.is_private() || value.is_link_local() || value.octets()[0] == 0
                }
                IpAddr::V6(value) => {
                    value.is_unique_local()
                        || value.is_unicast_link_local()
                        || value
                            .to_ipv4_mapped()
                            .is_some_and(|v4| is_private_or_local_host(&v4.to_string()))
                }
            };
    }
    false
}

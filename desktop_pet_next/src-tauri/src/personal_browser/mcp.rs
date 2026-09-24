//! Device-local, persistent stdio transport for the pinned official Chrome adapter.
//! No shell, remote debugging flags, cookie export, or browser process termination.
use std::{collections::HashSet, io::{BufRead, BufReader, Write}, path::PathBuf,
    process::{Child, ChildStdin, Command, Stdio}, sync::mpsc, time::{Duration, Instant}};
use serde_json::{json, Value};
#[cfg(windows)] use std::os::windows::{io::AsRawHandle, process::CommandExt};

pub struct Client {
    child: Child, stdin: ChildStdin, receiver: mpsc::Receiver<Value>, next_id: u64,
    pub tools: HashSet<String>,
    pending_list: Option<u64>,
    #[cfg(windows)] job: windows::Win32::Foundation::HANDLE,
}
// The job handle is owned and only closed when this client is dropped under its mutex.
#[cfg(windows)] unsafe impl Send for Client {}
impl Client {
    pub fn start() -> Result<Self, &'static str> {
        let node = std::env::var_os("PATH").into_iter().flat_map(|v| std::env::split_paths(&v).collect::<Vec<_>>())
            .map(|p| p.join(if cfg!(windows) {"node.exe"} else {"node"})).find(|p| p.is_file()).ok_or("chrome_adapter_node_missing")?;
        let npx = node.parent().unwrap_or(&PathBuf::new()).join("node_modules/npm/bin/npx-cli.js");
        if !npx.is_file() { return Err("chrome_adapter_npm_missing"); }
        let mut command = Command::new(node);
        command.arg(npx).args(["--offline", "--yes", "chrome-devtools-mcp@1.9.0", "--autoConnect",
            "--no-usage-statistics", "--no-performance-crux", "--no-category-network", "--no-category-performance",
            "--no-category-emulation", "--no-javascript-evaluation", "--experimentalStructuredContent",
            "--screenshot-max-width=1280", "--screenshot-max-height=960"])
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
        #[cfg(windows)] command.creation_flags(0x08000000); // CREATE_NO_WINDOW
        let mut child = command.spawn().map_err(|_| "chrome_adapter_start_failed")?;
        #[cfg(windows)] let job = match unsafe { job_for(&child) } {
            Ok(job) => job, Err(reason) => { let _ = child.kill(); let _ = child.wait(); return Err(reason); }
        };
        let stdin = child.stdin.take().ok_or("chrome_adapter_stdin_missing")?;
        let stdout = child.stdout.take().ok_or("chrome_adapter_stdout_missing")?;
        let (sender, receiver) = mpsc::sync_channel(8);
        std::thread::spawn(move || {
            let mut reader = BufReader::new(stdout);
            loop {
                // fill_buf bounds memory even for a broken adapter's unterminated line.
                let mut line = Vec::new();
                loop {
                    let Ok(bytes) = reader.fill_buf() else { return; };
                    if bytes.is_empty() { return; }
                    let end = bytes.iter().position(|v| *v == b'\n').map(|i| i + 1).unwrap_or(bytes.len());
                    if line.len() + end > 12 * 1024 * 1024 { return; }
                    let done = bytes[end - 1] == b'\n'; line.extend_from_slice(&bytes[..end]); reader.consume(end);
                    if done { break; }
                }
                if let Ok(value) = serde_json::from_slice::<Value>(&line) {
                    if value.get("id").is_some() && sender.send(value).is_err() { return; }
                }
            }
        });
        let mut client = Self {child, stdin, receiver, next_id:0, tools:HashSet::new(), pending_list:None, #[cfg(windows)] job};
        client.request("initialize", json!({"protocolVersion":"2025-03-26","capabilities":{},
            "clientInfo":{"name":"Akane personal browser","version":"1.0.0"}}), Duration::from_secs(15))?;
        client.write(&json!({"jsonrpc":"2.0","method":"notifications/initialized"}))?;
        let discovered = client.request("tools/list", json!({}), Duration::from_secs(5))?;
        for tool in discovered["tools"].as_array().ok_or("chrome_adapter_tools_invalid")? {
            if let Some(name) = tool["name"].as_str() { client.tools.insert(name.into()); }
        }
        if !["list_pages","select_page","take_snapshot","take_screenshot","click","fill","press_key","navigate_page"].iter().all(|t| client.tools.contains(*t)) {
            return Err("chrome_adapter_contract_unsupported");
        }
        Ok(client)
    }
    fn write(&mut self, message: &Value) -> Result<(), &'static str> {
        writeln!(self.stdin, "{message}").and_then(|_| self.stdin.flush()).map_err(|_| "chrome_adapter_disconnected")
    }
    fn request(&mut self, method: &str, params: Value, timeout: Duration) -> Result<Value, &'static str> {
        self.next_id += 1; let id = self.next_id;
        self.write(&json!({"jsonrpc":"2.0","id":id,"method":method,"params":params}))?;
        self.receive(id, timeout)
    }
    fn receive(&mut self, id:u64, timeout:Duration)->Result<Value,&'static str> {
        let deadline = Instant::now() + timeout;
        loop {
            let remaining = deadline.checked_duration_since(Instant::now()).ok_or("chrome_adapter_timeout")?;
            let response = self.receiver.recv_timeout(remaining).map_err(|e|match e {
                mpsc::RecvTimeoutError::Timeout=>"chrome_adapter_timeout",
                mpsc::RecvTimeoutError::Disconnected=>"chrome_adapter_disconnected",
            })?;
            if response["id"] != id { continue; }
            if response.get("error").is_some() { return Err("chrome_adapter_protocol_error"); }
            return response.get("result").cloned().ok_or("chrome_adapter_protocol_error");
        }
    }
    pub fn call(&mut self, name: &str, args: Value) -> Result<Value, &'static str> {
        if !self.tools.contains(name) { return Err("chrome_action_unsupported"); }
        // Chrome's connection consent can outlast one tool call. Retain the
        // original read request and adapter so the next list call collects its
        // response, instead of killing it and spawning another permission prompt.
        if name == "list_pages" {
            let id=if let Some(id)=self.pending_list.take(){id}else{
                self.next_id+=1;let id=self.next_id;
                self.write(&json!({"jsonrpc":"2.0","id":id,"method":"tools/call","params":{"name":name,"arguments":args}}))?;
                id
            };
            return match self.receive(id,Duration::from_secs(10)) {
                Err("chrome_adapter_timeout")=>{self.pending_list=Some(id);Err("chrome_connection_pending_check_browser")},
                result=>result,
            };
        }
        if self.pending_list.is_some(){return Err("chrome_connection_pending_check_browser");}
        self.request("tools/call", json!({"name":name,"arguments":args}), Duration::from_secs(12))
    }
}
impl Drop for Client {
    fn drop(&mut self) {
        // Killing only this MCP job detaches Puppeteer from the user's external Chrome.
        #[cfg(windows)] unsafe { let _ = windows::Win32::Foundation::CloseHandle(self.job); }
        let _ = self.child.kill(); let _ = self.child.wait();
    }
}
#[cfg(windows)] unsafe fn job_for(child: &Child) -> Result<windows::Win32::Foundation::HANDLE, &'static str> {
    use windows::Win32::{Foundation::{CloseHandle,HANDLE}, System::JobObjects::*};
    let job = CreateJobObjectW(None, None).map_err(|_| "chrome_adapter_job_failed")?;
    let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    let result = SetInformationJobObject(job, JobObjectExtendedLimitInformation,
        &limits as *const _ as *const _, std::mem::size_of_val(&limits) as u32)
        .and_then(|_| AssignProcessToJobObject(job, HANDLE(child.as_raw_handle())));
    if result.is_err() { let _ = CloseHandle(job); return Err("chrome_adapter_job_failed"); }
    Ok(job)
}

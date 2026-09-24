//! One physical desktop: serialize live requests and bound idle task ownership.
use std::{ops::{Deref,DerefMut}, time::{Duration,Instant}, sync::{Mutex, MutexGuard, OnceLock, atomic::{AtomicBool,Ordering}}};
const IDLE_TIMEOUT:Duration=Duration::from_secs(60);
static LEASE: OnceLock<Mutex<Lease>> = OnceLock::new();
static STOPPED: AtomicBool = AtomicBool::new(false);
static ACTIVE_EPOCH: OnceLock<Mutex<String>> = OnceLock::new();
pub fn begin_connection(epoch:&str) {
    if let Ok(mut current)=ACTIVE_EPOCH.get_or_init(Default::default).lock(){
        if !current.is_empty() && current.as_str()!=epoch {stop();}
        *current=epoch.into();
    }
}
pub fn epoch_valid(epoch:&str)->bool {
    ACTIVE_EPOCH.get_or_init(Default::default).lock().map(|current|current.is_empty()||current.as_str()==epoch).unwrap_or(false)
}
#[derive(Default)]
pub struct Lease { tool: String, scope: String, epoch: String, generation: u64, pub revision: u64, last_finished:Option<Instant> }
pub struct LeaseGuard(MutexGuard<'static,Lease>);
impl Deref for LeaseGuard {type Target=Lease;fn deref(&self)->&Lease{&self.0}}
impl DerefMut for LeaseGuard {fn deref_mut(&mut self)->&mut Lease{&mut self.0}}
impl Drop for LeaseGuard {
    // Count idle time from completion, never from the start of a slow request.
    fn drop(&mut self){self.0.last_finished=Some(Instant::now());}
}
fn generation() -> u64 { crate::computer_use::user_input_generation() }
pub fn stopped() -> bool { STOPPED.load(Ordering::SeqCst) }
pub fn stop() { STOPPED.store(true,Ordering::SeqCst); }
pub fn release(tool:&str,scope:&str,epoch:&str) {
    if let Ok(mut lease)=LEASE.get_or_init(Default::default).try_lock() {
        if lease.tool==tool && lease.scope==scope && lease.epoch==epoch {lease.tool.clear();lease.revision+=1;}
    }
}
pub fn resume() -> Result<(), &'static str> {
    let mut lease = LEASE.get_or_init(Default::default).try_lock().map_err(|_| "desktop_busy")?;
    if STOPPED.load(Ordering::SeqCst) { lease.tool.clear(); }
    lease.generation = generation(); lease.revision += 1; STOPPED.store(false,Ordering::SeqCst); Ok(())
}
pub fn acquire(tool: &str, scope: &str, epoch: &str, establish: bool) -> Result<LeaseGuard, &'static str> {
    acquire_with_mode(tool,scope,epoch,establish,false,false)
}
/// Reading cannot resume input or acknowledge a changed input generation.
pub fn acquire_read(tool: &str, scope: &str, epoch: &str, establish: bool) -> Result<LeaseGuard, &'static str> {
    acquire_with_mode(tool,scope,epoch,establish,true,false)
}
/// Only a fresh, completed observation may acknowledge incidental user activity.
/// It cannot release an explicit stop, steal another task/tool's lease, or enable input.
pub fn acknowledge_observation(tool:&str,scope:&str,epoch:&str,observed_generation:u64)->Result<(), &'static str> {
    // A local resume may have cleared the lease while retaining this control.
    // Observation can reacquire an unowned lease, but never replace another scope.
    let mut lease=acquire_with_mode(tool,scope,epoch,true,true,true)?;
    if stopped(){return Err("stopped_requires_local_resume");}
    if generation()!=observed_generation{return Err("user_takeover");}
    #[cfg(windows)]
    if !crate::computer_use::input_quiet(){return Err("user_input_active");}
    lease.generation=observed_generation;lease.revision+=1;
    Ok(())
}
fn acquire_with_mode(tool: &str, scope: &str, epoch: &str, establish: bool, read_only:bool, observation_only:bool) -> Result<LeaseGuard, &'static str> {
    if !epoch_valid(epoch) {return Err("device_epoch_expired");}
    let mut lease = LEASE.get_or_init(Default::default).try_lock().map_err(|_| "desktop_busy")?;
    if observation_only && !lease.tool.is_empty() && lease.scope!=scope {return Err("desktop_owned_by_other_session");}
    lease.claim(tool,scope,epoch,establish,read_only,stopped(),generation(),Instant::now())?;
    Ok(LeaseGuard(lease))
}
impl Lease {
    fn idle_expired(&self,now:Instant)->bool {
        self.last_finished.is_some_and(|finished|now.saturating_duration_since(finished)>=IDLE_TIMEOUT)
    }
    fn claim(&mut self,tool:&str,scope:&str,epoch:&str,establish:bool,read_only:bool,stopped:bool,generation:u64,now:Instant)->Result<(), &'static str>{
        if stopped && !read_only{return Err("stopped_requires_local_resume");}
        if !self.tool.is_empty() && self.epoch==epoch {
            if self.scope==scope {
                if self.generation!=generation && !read_only{return Err("user_takeover");}
                if self.tool!=tool{return Err("tool_handoff_required");}
                return Ok(());
            }
            if stopped || !self.idle_expired(now){return Err("desktop_owned_by_other_session");}
        }
        // Old observations/inputs cannot acquire ownership. A new selection,
        // explicit window operation, app launch or browser connection must do it.
        if !establish{return Err("control_reselection_required");}
        self.tool=tool.into();self.scope=scope.into();self.epoch=epoch.into();
        self.generation=generation;self.revision+=1;Ok(())
    }
    pub fn input_generation(&self)->u64 {self.generation}
    pub fn check(&self) -> Result<(), &'static str> {
        if stopped() { Err("stopped") } else if self.generation != generation() { Err("user_takeover") } else { Ok(()) }
    }
    pub fn handoff(&mut self, to: &str) {
        self.tool = to.into(); self.revision += 1;
    }
}

pub fn retry_after_ms()->Option<u64>{
    let lease=LEASE.get_or_init(Default::default).try_lock().ok()?;
    Some(IDLE_TIMEOUT.saturating_sub(lease.last_finished?.elapsed()).as_millis() as u64)
}

#[cfg(test)]
pub(crate) fn expire_idle_for_test(){
    LEASE.get_or_init(Default::default).lock().unwrap().last_finished=Some(Instant::now()-IDLE_TIMEOUT);
}

#[cfg(test)] mod tests {
    use super::*;
    #[test] fn ownership_and_handoff() {
        resume().unwrap();
        let mut a = acquire("computer_use","one","test",true).unwrap();
        assert!(matches!(acquire("browser_page","one","test",true),Err("desktop_busy")));
        a.last_finished=Some(Instant::now()-IDLE_TIMEOUT);
        assert!(matches!(acquire("computer_use","two","test",true),Err("desktop_busy")),"even an old timestamp cannot steal an executing request");
        a.handoff("browser_page"); drop(a);
        assert!(matches!(acquire("computer_use","one","test",true),Err("tool_handoff_required")));
        assert!(matches!(acquire("browser_page","two","test",true),Err("desktop_owned_by_other_session")));
        let b = acquire("browser_page","one","test",false).unwrap(); drop(b);
        stop(); assert!(matches!(acquire("browser_page","one","test",true),Err("stopped_requires_local_resume")));
        assert!(acquire_read("browser_page","one","test",false).is_ok());
        assert!(matches!(acquire_read("browser_page","two","test",false),Err("desktop_owned_by_other_session")));
        resume().unwrap(); assert!(acquire("browser_page","one","test",true).is_ok());
    }

    #[test] fn idle_owner_expires_without_replaying_old_evidence(){
        let now=Instant::now();let mut lease=Lease::default();
        lease.claim("computer_use","master","epoch",true,false,false,0,now).unwrap();
        lease.last_finished=Some(now);
        assert_eq!(lease.claim("computer_use","group","epoch",true,false,false,0,now+Duration::from_secs(59)),Err("desktop_owned_by_other_session"));
        assert_eq!(lease.last_finished,Some(now),"rejected contenders cannot extend the owner's idle interval");
        let expired=now+IDLE_TIMEOUT;
        assert_eq!(lease.claim("computer_use","group","epoch",false,false,false,0,expired),Err("control_reselection_required"));
        assert_eq!(lease.claim("computer_use","group","epoch",false,true,false,0,expired),Err("control_reselection_required"),"observe cannot claim an expired owner's lease");
        let revision=lease.revision;
        lease.claim("computer_use","group","epoch",true,false,false,0,expired).unwrap();
        assert!(lease.revision>revision);lease.last_finished=Some(expired);
        assert_eq!(lease.claim("computer_use","master","epoch",false,false,false,0,expired),Err("desktop_owned_by_other_session"));
        assert_eq!(lease.claim("computer_use","master","epoch",false,false,false,0,expired+IDLE_TIMEOUT),Err("control_reselection_required"));
    }

    #[test] fn idle_expiry_preserves_stop_activity_and_tool_handoff(){
        let now=Instant::now();let mut lease=Lease::default();
        lease.claim("computer_use","master","epoch",true,false,false,0,now).unwrap();
        lease.last_finished=Some(now-IDLE_TIMEOUT);
        assert_eq!(lease.claim("computer_use","group","epoch",true,false,true,0,now),Err("stopped_requires_local_resume"));
        assert_eq!(lease.claim("browser_page","group","epoch",true,true,true,0,now),Err("desktop_owned_by_other_session"));
        assert_eq!(lease.claim("computer_use","master","epoch",true,false,false,1,now),Err("user_takeover"));
        assert_eq!(lease.claim("browser_page","master","epoch",true,true,false,0,now),Err("tool_handoff_required"));
        lease.claim("browser_page","group","epoch",true,true,false,0,now).unwrap();
        assert_eq!(lease.tool,"browser_page");
    }
}

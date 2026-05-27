!macro NSIS_HOOK_PREINSTALL
  ; Force close the backend process to prevent file locking issues
  ; nsExec::Exec runs the command without a console window
  nsExec::Exec 'taskkill /F /IM rivalnxt_backend.exe'
  ; User data is preserved during reinstall/add components
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ; Force close the backend process before uninstall
  nsExec::Exec 'taskkill /F /IM rivalnxt_backend.exe'
  
  ; User data is explicitly preserved during uninstall, as requested.
  ; Deleting user data is only done when triggered manually via the app UI.
!macroend


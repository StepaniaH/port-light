/* Temporary inversion point while views migrate out of app.js slice-by-slice.
   app.js binds the real implementations at boot; modules call through here so
   no view module needs to import the entry. */

export const bridge = {
  closeDetail: () => {},
  showPortDetail: () => {},
  renderDetail: () => {},
  loadSettingsPage: () => {},
  showSettingsPanel: () => {},
  revertUnsavedSettings: () => {},
};

// js/firebase.js
// Reuses the same Firebase project as Kravas-vilcienu-karte (ttains)
// No setup needed — Firebase Realtime DB is schemaless.
// Used codes are stored at: /usedPromoCodes/{voterId}/{codeId}

const firebaseConfig = {
    apiKey: "AIzaSyBC3YVk95KyuXgPCvVmPuNDNpt1ZgWQaYA",
        authDomain: "ttains.firebaseapp.com",
            databaseURL: "https://ttains-default-rtdb.europe-west1.firebasedatabase.app",
                projectId: "ttains",
                    storageBucket: "ttains.firebasestorage.app",
                        messagingSenderId: "40497612515",
                            appId: "1:40497612515:web:a8bc18a87d1c8eb88ba7d6"
                            };

                            // Only initialize if not already done (in case both projects share a page someday)
                            if (!firebase.apps || !firebase.apps.length) {
                                firebase.initializeApp(firebaseConfig);
                                }
                                const db = firebase.database();

                                /**
                                 * Get or create a stable anonymous ID for this browser (same logic as train map).
                                  */
                                  function getVoterId() {
                                      const key = 'kravasMapVoterId'; // reuse same key — same device = same identity
                                          try {
                                                  let id = localStorage.getItem(key);
                                                          if (!id) {
                                                                      id = crypto.randomUUID
                                                                                      ? crypto.randomUUID()
                                                                                                      : `voter-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
                                                                                                                  localStorage.setItem(key, id);
                                                                                                                          }
                                                                                                                                  return id;
                                                                                                                                      } catch (_) {
                                                                                                                                              return `voter-fallback-${Date.now()}`;
                                                                                                                                                  }
                                                                                                                                                  }

                                                                                                                                                  /**
                                                                                                                                                   * Load all used promo code IDs for this device from Firebase.
                                                                                                                                                    * Returns a Set of codeId strings.
                                                                                                                                                     */
                                                                                                                                                     function loadUsedPromos() {
                                                                                                                                                         const voterId = getVoterId();
                                                                                                                                                             return db.ref(`usedPromoCodes/${voterId}`).once('value')
                                                                                                                                                                     .then(snap => {
                                                                                                                                                                                 const val = snap.val() || {};
                                                                                                                                                                                             return new Set(Object.keys(val));
                                                                                                                                                                                                     })
                                                                                                                                                                                                             .catch(err => {
                                                                                                                                                                                                                         console.warn('[Firebase] Could not load used promos:', err);
                                                                                                                                                                                                                                     return new Set();
                                                                                                                                                                                                                                             });
                                                                                                                                                                                                                                             }

                                                                                                                                                                                                                                             /**
                                                                                                                                                                                                                                              * Mark a promo code as used for this device.
                                                                                                                                                                                                                                               * @param {string} codeId - the unique code ID from codes.json
                                                                                                                                                                                                                                                */
                                                                                                                                                                                                                                                function markPromoUsed(codeId) {
                                                                                                                                                                                                                                                    const voterId = getVoterId();
                                                                                                                                                                                                                                                        return db.ref(`usedPromoCodes/${voterId}/${codeId}`).set({
                                                                                                                                                                                                                                                                usedAt: new Date().toISOString()
                                                                                                                                                                                                                                                                    }).catch(err => console.warn('[Firebase] Could not save used promo:', err));
                                                                                                                                                                                                                                                                    }
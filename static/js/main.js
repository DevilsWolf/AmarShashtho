document.addEventListener('DOMContentLoaded', function() {
    
    // Logic for signup form admin secret field
    const adminCheckbox = document.getElementById('admin_checkbox');
    const adminSecretField = document.getElementById('admin_secret_field');
    
    if (adminCheckbox && adminSecretField) {
        adminCheckbox.addEventListener('change', function() {
            if (this.checked) {
                adminSecretField.classList.remove('hidden');
                adminSecretField.required = true;
            } else {
                adminSecretField.classList.add('hidden');
                adminSecretField.required = false;
                adminSecretField.value = '';
            }
        });
    }

    // Logic for emergency button
    const emergencyBtn = document.getElementById('emergency-btn');
    if (emergencyBtn) {
        emergencyBtn.addEventListener('click', function() {
            alert('আপনার লোকেশন অ্যাক্সেস করার চেষ্টা করা হচ্ছে। অ্যাম্বুলেন্স খুঁজতে দয়া করে অনুমতি দিন।');
            
            if (navigator.geolocation) {
                navigator.geolocation.getCurrentPosition(showPosition, showError);
            } else {
                alert("Geolocation is not supported by this browser.");
                // Fallback search
                openMapSearch(null);
            }
        });
    }

    function showPosition(position) {
        const lat = position.coords.latitude;
        const lon = position.coords.longitude;
        console.log(`Location found: ${lat}, ${lon}`);
        openMapSearch(`${lat},${lon}`);
    }

    function showError(error) {
        switch(error.code) {
            case error.PERMISSION_DENIED:
                alert("আপনি লোকেশন অ্যাক্সেস করার অনুমতি দেননি।");
                break;
            case error.POSITION_UNAVAILABLE:
                alert("আপনার লোকেশন তথ্য পাওয়া যাচ্ছে না।");
                break;
            case error.TIMEOUT:
                alert("লোকেশন খোঁজার অনুরোধটির সময় শেষ হয়েছে।");
                break;
            case error.UNKNOWN_ERROR:
                alert("একটি অজানা ত্রুটি ঘটেছে।");
                break;
        }
        // Fallback search even on error
        openMapSearch(null);
    }

    function openMapSearch(location) {
        let query;
        if (location) {
            query = `Ambulance near me`; // Google Maps understands this with coordinates
        } else {
            query = `অ্যাম্বুল্যান্স সার্ভিস`; // General search if location fails
        }
        
        const googleMapsUrl = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(query)}${location ? `&ll=${location}` : ''}`;
        
        window.open(googleMapsUrl, '_blank');
    }

});
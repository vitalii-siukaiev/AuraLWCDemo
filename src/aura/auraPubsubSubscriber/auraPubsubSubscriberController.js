({
    handlePubsubReady: function(component) {
        var pubsub = component.find('pubsub');
        var callback = $A.getCallback(function(eventData) {
            component.set('v.lwcEventData', JSON.stringify(eventData));
            //eventData.firstName = 'Bart';
        });
        pubsub.registerListener('auraButtonClick', callback);
    },

    handleDestroy: function(component) {
        var pubsub = component.find('pubsub');
        pubsub.unregisterAllListeners();
    }
});
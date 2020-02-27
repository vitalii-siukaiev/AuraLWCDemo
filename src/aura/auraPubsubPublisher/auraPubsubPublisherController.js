({
    handleButtonClick: function(component, event) {
        const pubsub = component.find('pubsub');
        const data = {firstName: 'Homer', lastName: 'Simpson'};
        pubsub.fireEvent('auraButtonClick', data);
    }
});
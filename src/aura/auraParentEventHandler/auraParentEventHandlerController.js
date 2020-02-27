
({
    handleEvent: function(component, event, helper) {
        var param = event.getParam('param');
        //param.firstName = 'Lisa';
        //param.lastName = 'Simpson';
        component.set('v.auraParams', param);
    }
});
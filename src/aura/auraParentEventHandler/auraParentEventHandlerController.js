
({
    handleEvent: function(component, event, helper) {
        var param = event.getParam('param');

        param.firstName = 'Lisa';
        param.lastName = 'Simpson';
        param.additional.age = 11;
        param.additional.gender = 'female';

        component.set('v.auraParams', param);
    }
});
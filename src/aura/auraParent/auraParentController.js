
({
    handleButtonClick: function(component, event, helper) {
        component.find('childComponent').childMethod({firstName : 'John', lastName: 'Week'});
    }
});
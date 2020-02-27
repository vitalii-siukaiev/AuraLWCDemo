/**
 * Created by vsiukaiev on 24-Feb-20.
 */

({
    handleBottomIsCalling: function(component, event, helper) {
        const param = event.getParams('data')
        component.set("v.eventData", param.data);
        $A.util.addClass(component.find('lwcComponent'), 'blue');
    }
});
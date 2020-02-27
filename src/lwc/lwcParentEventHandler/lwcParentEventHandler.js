
import { LightningElement, track } from 'lwc';

export default class LWC_ParentEventHandler extends LightningElement {
    @track parentParams;

    renderedCallback(){
        this.template.querySelector('c-lwc-child-event').addEventListener(
            'customevent',
            this.handleEvent.bind(this)
        );
    }

    handleEvent(event) {
        this.parentParams = event.detail.param;
    }

}